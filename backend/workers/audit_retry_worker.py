"""AuditRetryWorker — drains the audit:dead_letter Redis stream (REQ-M8-05).

Reads failed audit emit payloads from the ``audit:dead_letter`` stream and
re-emits them via ``AuditEventService.emit_blocking``. On successful re-emit the entry
is XDEL'd and the ``AUDIT_DEAD_LETTER_DEPTH`` Prometheus gauge is decremented, so the
depth metric reflects the true backlog rather than the all-time failure count.

``emit_blocking``, not ``emit``: the retry path must await the write. ``emit`` is
fire-and-forget, so it reports no success (nothing to gate the XDEL on) and applies no
backpressure (the loop would spawn writes faster than they complete).

PII-redaction runs again on the retry path (T-M8-11) because payloads are re-emitted
through audit_service.emit_blocking → _redact_payload → _write.

Usage (standalone process):
    python -m workers.audit_retry_worker

Usage (from code):
    worker = AuditRetryWorker()
    await worker.run()           # blocking consume loop
    await worker.process_one()  # single-message drain (used by unit tests)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

from config.env import REDIS_URL
from shared.audit.models import AuditEventPayload
from shared.audit.service import audit_service as _default_audit_service
from shared.services.metrics import AUDIT_DEAD_LETTER_DEPTH

logger = logging.getLogger(__name__)

_DEAD_LETTER_STREAM = "audit:dead_letter"
# Entries per read. Each is awaited to completion before the next batch, so this
# bounds how many audit writes are in flight at once.
_BATCH_SIZE = 10
# How long xread parks when the stream is drained — this is what makes the loop idle
# instead of spin, and it only works because the cursor advances.
_BLOCK_MS = 5000


class AuditRetryWorker:
    """Drains the audit:dead_letter Redis stream and re-emits via AuditEventService.

    Constructor accepts optional ``audit_service`` and ``redis_client`` so that
    unit tests can inject mocks without patching module globals.
    """

    def __init__(
        self,
        audit_service: Any = None,
        redis_client: Any = None,
    ) -> None:
        self._svc = audit_service or _default_audit_service
        # Injected client for unit tests; when None a fresh client is created per run()
        self._redis_client = redis_client

    async def _process_message(self, client: Any, msg_id: Any, fields: dict) -> bool:
        """Re-emit one dead-letter entry. Returns True only if it is now durably stored.

        Uses emit_blocking, NOT emit. emit() is fire-and-forget — it schedules the write
        on a background task and returns immediately, so it can neither report success
        nor slow this loop down. Both properties matter here: without a success signal
        there is no safe moment to XDEL, and without backpressure the loop spawns write
        tasks faster than they can complete. emit_blocking awaits the write (and carries
        its own tenacity retry), so a True return means the row is committed.

        Redaction still runs on the retry path (T-M8-11): emit_blocking calls
        _redact_payload before _write, exactly as emit does.
        """
        payload_bytes = fields.get(b"payload") or fields.get("payload")
        if payload_bytes is None:
            # Unparseable entry — drop it, or it is re-read on every restart forever.
            logger.warning("Dead-letter message %s has no 'payload' field — dropping", msg_id)
            await client.xdel(_DEAD_LETTER_STREAM, msg_id)
            return False
        try:
            payload_str = (
                payload_bytes.decode() if isinstance(payload_bytes, bytes) else payload_bytes
            )
            payload = AuditEventPayload.model_validate_json(payload_str)
            await self._svc.emit_blocking(payload)
        except Exception as exc:
            # Leave the entry on the stream — a later run retries it. The read cursor
            # still advances past it, so one poison message cannot wedge the drain.
            logger.error(
                "Dead-letter retry failed for %s: %s", msg_id, type(exc).__name__
            )
            return False

        # Committed. Remove it so the backlog actually shrinks and a restart does not
        # replay it — the absence of this XDEL is what let the stream grow to a
        # permanent backlog that the worker re-read forever.
        await client.xdel(_DEAD_LETTER_STREAM, msg_id)
        AUDIT_DEAD_LETTER_DEPTH.dec()
        logger.info(
            "Dead-letter retry succeeded: run_id=%s event_type=%s",
            payload.run_id,
            payload.event_type,
        )
        return True

    async def process_one(self) -> bool:
        """Read and re-emit a single message from audit:dead_letter.

        Returns True if a message was processed, False if the stream was empty.
        Uses the injected redis_client when present (unit-test path) or creates
        a short-lived client when called standalone.

        Reads from "0-0" deliberately: this is the single-shot drain entry point, so
        "oldest surviving entry" is the right target. Successfully retried entries are
        XDEL'd, so repeated calls walk forward through the backlog.
        """
        client = self._redis_client
        own_client = False
        if client is None:
            client = aioredis.from_url(REDIS_URL)
            own_client = True
        try:
            results = await client.xread(
                streams={_DEAD_LETTER_STREAM: "0-0"},
                count=1,
            )
            if not results:
                return False
            for _stream_name, messages in results:
                for msg_id, fields in messages:
                    await self._process_message(client, msg_id, fields)
            return True
        finally:
            if own_client:
                await client.aclose()

    async def run(self) -> None:
        """Blocking consume loop — drains audit:dead_letter, then waits for new entries.

        The cursor (`last_id`) advances past every entry this loop has seen, including
        ones whose re-emit failed. Reading from a fixed "0-0" instead — as this loop
        originally did — means every iteration re-reads the same oldest entries, so the
        loop never blocks, never drains, and spawns write tasks continuously; observed
        in local dev as a permanently saturated CPU core with 918 entries stranded on
        the stream and nothing reaching the audit_events table.

        Entries that fail are left on the stream for a later process to retry; only the
        in-memory cursor moves past them, so a single bad payload cannot wedge the drain.
        """
        client = self._redis_client
        own_client = False
        if client is None:
            client = aioredis.from_url(REDIS_URL)
            own_client = True
        last_id: Any = "0-0"
        logger.info("AuditRetryWorker started — draining %s", _DEAD_LETTER_STREAM)
        try:
            while True:
                results = await client.xread(
                    streams={_DEAD_LETTER_STREAM: last_id},
                    count=_BATCH_SIZE,
                    block=_BLOCK_MS,
                )
                if not results:
                    # Caught up: xread blocked for _BLOCK_MS and returned nothing.
                    continue
                for _stream_name, messages in results:
                    for msg_id, fields in messages:
                        last_id = msg_id
                        await self._process_message(client, msg_id, fields)
        except asyncio.CancelledError:
            logger.info("AuditRetryWorker shutting down")
        finally:
            if own_client:
                await client.aclose()


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO)
    asyncio.run(AuditRetryWorker().run())
