"""AuditEventService — single write path for all audit_events inserts (D-02).

All audit writes (agents + connectors) route through this service. PII redaction
(D-M8-03), dead-letter fallback (D-M8-05), and write-duration metric all live here.

emit()          — fire-and-forget; failures go to Redis dead-letter stream, never raises
emit_blocking() — awaited with tenacity retry; raises on persistent failure (HITL path, D-06)
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import Any

import redis.asyncio as aioredis
from tenacity import retry, stop_after_attempt, wait_exponential

from config.env import REDIS_URL
from shared.audit.models import AuditEventPayload
from shared.db import get_db_session_for_tenant
from shared.models.orm import AuditEvent
from shared.services.metrics import AUDIT_DEAD_LETTER_DEPTH, AUDIT_WRITE_DURATION

_PII_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)
_DEAD_LETTER_STREAM = "audit:dead_letter"


class AuditEventService:
    """Single write path for all audit_events inserts.

    _redact_pii and _write are independently unit-testable — they do not require
    a running event loop (Pitfall 4 guard: only emit/emit_blocking use asyncio).
    """

    def _redact_pii(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Walk payload dict recursively and replace email-pattern strings with [REDACTED].

        Non-string scalars (int, float, bool, None) pass through unchanged.
        Recurses into nested dicts and processes list elements.
        """
        redacted: dict[str, Any] = {}
        for k, v in payload.items():
            if isinstance(v, str):
                redacted[k] = _PII_EMAIL_RE.sub("[REDACTED]", v)
            elif isinstance(v, dict):
                redacted[k] = self._redact_pii(v)
            elif isinstance(v, list):
                redacted[k] = [
                    _PII_EMAIL_RE.sub("[REDACTED]", item) if isinstance(item, str) else item
                    for item in v
                ]
            else:
                redacted[k] = v
        return redacted

    def _redact_payload(self, payload: AuditEventPayload) -> AuditEventPayload:
        """Return a new AuditEventPayload with PII-scrubbed payload field.

        Produces a clean copy so tests can assert on the argument passed to _write
        (the raw payload is never stored — D-M8-03 / T-M8-04 mitigation).
        """
        return payload.model_copy(update={"payload": self._redact_pii(payload.payload or {})})

    async def _write(self, payload: AuditEventPayload) -> None:
        """DB write using the already-redacted AuditEventPayload.

        Uses get_db_session_for_tenant(tenant_id) — SET LOCAL app.current_tenant_id
        ensures RLS FORCE constrains the insert to the correct tenant (T-M8-06).
        run_id and agent_type are folded into the payload JSONB; resource_id stores
        run_id (the ORM has no dedicated run_id column — Pitfall 6 resolution).

        Callers (emit, emit_blocking) must pass a payload that has already been through
        _redact_payload(); _write does NOT re-redact to keep the interface explicit.
        """
        start = time.monotonic()
        async with get_db_session_for_tenant(str(payload.tenant_id)) as session:
            session.add(
                AuditEvent(
                    id=uuid.uuid4(),
                    tenant_id=uuid.UUID(str(payload.tenant_id)),
                    actor_id=payload.actor_id,
                    event_type=payload.event_type,
                    resource_type=payload.resource_type,
                    resource_id=payload.resource_id or payload.run_id,
                    payload={
                        **payload.payload,
                        "run_id": str(payload.run_id) if payload.run_id else None,
                        "agent_type": payload.agent_type,
                    },
                )
            )
        elapsed_ms = (time.monotonic() - start) * 1000
        # observe() takes seconds; convert ms to seconds for consistent Prometheus units
        AUDIT_WRITE_DURATION.labels(agent_type=payload.agent_type or "unknown").observe(
            elapsed_ms / 1000
        )

    async def _send_to_dead_letter(self, payload: AuditEventPayload) -> None:
        """XADD failed emit to the dead-letter stream for the retry worker.

        Swallows all exceptions — dead-letter failure must never propagate to the caller
        (the emit() contract guarantees no raise under any condition).
        """
        try:
            client = aioredis.from_url(REDIS_URL)
            try:
                await client.xadd(
                    _DEAD_LETTER_STREAM,
                    {"payload": payload.model_dump_json()},
                )
                AUDIT_DEAD_LETTER_DEPTH.inc()
            finally:
                await client.aclose()
        except Exception:
            pass

    async def emit(self, payload: AuditEventPayload) -> None:
        """Fire-and-forget emit. Failures go to dead-letter stream; never raises.

        PII redaction runs before _write is called — the raw payload is never stored.
        asyncio.create_task schedules _attempt on the running event loop; the subsequent
        sleep(0) yields so the task starts before emit() returns (allows unit tests to
        assert on mock calls without an extra sleep). _attempt swallows all exceptions.
        """
        clean = self._redact_payload(payload)

        async def _attempt() -> None:
            try:
                await self._write(clean)
            except Exception:
                await self._send_to_dead_letter(payload)

        asyncio.create_task(_attempt())
        await asyncio.sleep(0)  # yield so task starts before emit() returns

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def emit_blocking(self, payload: AuditEventPayload) -> None:
        """Blocking emit with tenacity retry. Raises on persistent failure (HITL path, D-06).

        Caller (signals.py) wraps this in asyncio.wait_for(timeout=30) to guard against
        deadlock (Pitfall 7).
        """
        await self._write(self._redact_payload(payload))


# Module-level singleton — imported by AuditCallbackHandler, connector audit_emitter,
# and signals.py HITL blocking emit path (D-02).
audit_service = AuditEventService()
