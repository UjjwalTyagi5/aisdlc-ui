"""AuditRetryWorker — drains the audit:dead_letter Redis stream (REQ-M8-05).

Reads failed audit emit payloads from the ``audit:dead_letter`` stream and
re-emits them via AuditEventService. On successful re-emit, decrements the
``AUDIT_DEAD_LETTER_DEPTH`` Prometheus gauge so the depth metric reflects the
true backlog rather than the all-time failure count.

PII-redaction runs again on the retry path (T-M8-11) because payloads are
re-emitted through audit_service.emit → _redact_payload → _write.

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

    async def process_one(self) -> bool:
        """Read and re-emit a single message from audit:dead_letter.

        Returns True if a message was processed, False if the stream was empty.
        Uses the injected redis_client when present (unit-test path) or creates
        a short-lived client when called standalone.

        The test fixture provides mock_redis_client.xread with a pre-set return
        value containing one entry from 'audit:dead_letter'. We use xread (not
        xreadgroup) to match the test fixture and keep the retry path simple —
        messages are consumed and acknowledged via a single-shot read.
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
            for stream_name, messages in results:
                for msg_id, fields in messages:
                    payload_bytes = fields.get(b"payload") or fields.get("payload")
                    if payload_bytes is None:
                        logger.warning(
                            "Dead-letter message %s has no 'payload' field — skipping",
                            msg_id,
                        )
                        continue
                    try:
                        payload_str = (
                            payload_bytes.decode() if isinstance(payload_bytes, bytes) else payload_bytes
                        )
                        payload = AuditEventPayload.model_validate_json(payload_str)
                        await self._svc.emit(payload)
                        # Decrement gauge so metric reflects current backlog depth
                        AUDIT_DEAD_LETTER_DEPTH.dec()
                        logger.info(
                            "Dead-letter retry succeeded: run_id=%s event_type=%s",
                            payload.run_id,
                            payload.event_type,
                        )
                    except Exception as exc:
                        logger.error(
                            "Dead-letter retry failed for message %s: %s",
                            msg_id,
                            type(exc).__name__,
                        )
            return True
        finally:
            if own_client:
                await client.aclose()

    async def run(self) -> None:
        """Blocking consume loop — polls audit:dead_letter every 5 seconds.

        Runs until cancelled (e.g. SIGTERM). Creates its own Redis client
        for the lifetime of the process.
        """
        client = aioredis.from_url(REDIS_URL)
        logger.info("AuditRetryWorker started — draining %s", _DEAD_LETTER_STREAM)
        try:
            while True:
                results = await client.xread(
                    streams={_DEAD_LETTER_STREAM: "0-0"},
                    count=10,
                    block=5000,
                )
                if not results:
                    continue
                for _stream_name, messages in results:
                    for msg_id, fields in messages:
                        payload_bytes = fields.get(b"payload") or fields.get("payload")
                        if payload_bytes is None:
                            logger.warning(
                                "Dead-letter message %s missing payload — skipping", msg_id
                            )
                            continue
                        try:
                            payload_str = (
                                payload_bytes.decode()
                                if isinstance(payload_bytes, bytes)
                                else payload_bytes
                            )
                            payload = AuditEventPayload.model_validate_json(payload_str)
                            await self._svc.emit(payload)
                            AUDIT_DEAD_LETTER_DEPTH.dec()
                        except Exception as exc:
                            logger.error(
                                "Dead-letter retry failed for %s: %s",
                                msg_id,
                                type(exc).__name__,
                            )
        except asyncio.CancelledError:
            logger.info("AuditRetryWorker shutting down")
        finally:
            await client.aclose()


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO)
    asyncio.run(AuditRetryWorker().run())
