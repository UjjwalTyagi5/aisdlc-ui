"""REQ-M8-05 — Dead-letter stream unit tests.

Asserts the failed-emit → Redis dead-letter → retry worker pipeline:
  - When AuditEventService._write() raises, _send_to_dead_letter() is called with XADD
  - The dead-letter Redis stream receives the payload JSON
  - A retry worker reads the stream and re-emits the event via AuditEventService.emit()

D-M8-05 locked decision: Failed non-blocking emits → Redis dead-letter stream
(reuse existing Redis infra). Background retry worker picks them up.

Wave-2 turns these GREEN when shared/audit/service.py and workers/audit_retry_worker.py
are implemented.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# Wave-2 implements shared/audit/service.py and workers/audit_retry_worker.py.
pytestmark = pytest.mark.xfail(
    reason="Wave 2 implements dead-letter service and retry worker",
    strict=False,
)


@pytest.mark.unit
async def test_failed_write_sends_to_dead_letter(mock_audit_service, mock_redis_client):
    """When _write() raises, _send_to_dead_letter() must XADD to audit:dead_letter."""
    from shared.audit.service import AuditEventService
    from shared.audit.models import AuditEventPayload

    svc = AuditEventService()

    # Force _write to raise a DB error
    svc._write = AsyncMock(side_effect=Exception("DB connection refused"))
    svc._send_to_dead_letter = AsyncMock(wraps=svc._send_to_dead_letter)

    payload = AuditEventPayload(
        tenant_id="00000000-0000-0000-0000-000000000001",
        run_id="run-dlq-001",
        event_type="tool_call_start",
        agent_type="requirements",
        actor_id="system:requirements",
        payload={"tool_name": "fetch_work_items"},
    )

    with patch("shared.audit.service.aioredis.from_url", return_value=mock_redis_client):
        await svc.emit(payload)

    # Give the background task a chance to run
    import asyncio
    await asyncio.sleep(0)

    mock_redis_client.xadd.assert_called_once()
    stream_name, fields = mock_redis_client.xadd.call_args[0]
    assert stream_name == "audit:dead_letter"
    assert "payload" in fields


@pytest.mark.unit
async def test_failed_write_does_not_raise(mock_audit_service, mock_redis_client):
    """emit() must never raise even when both _write() and dead-letter fail.

    fire-and-forget contract: failures are swallowed; the caller is never blocked.
    """
    from shared.audit.service import AuditEventService
    from shared.audit.models import AuditEventPayload

    svc = AuditEventService()
    svc._write = AsyncMock(side_effect=Exception("DB down"))

    payload = AuditEventPayload(
        tenant_id="00000000-0000-0000-0000-000000000001",
        run_id="run-dlq-002",
        event_type="model_call",
        agent_type="design",
        actor_id="system:design",
        payload={},
    )

    with patch("shared.audit.service.aioredis.from_url", return_value=mock_redis_client):
        # Must not raise
        await svc.emit(payload)

    import asyncio
    await asyncio.sleep(0)


@pytest.mark.unit
async def test_dead_letter_xadd_contains_payload_json(mock_redis_client):
    """The XADD message must include serialized AuditEventPayload JSON."""
    from shared.audit.service import AuditEventService
    from shared.audit.models import AuditEventPayload

    svc = AuditEventService()
    svc._write = AsyncMock(side_effect=RuntimeError("write error"))

    payload = AuditEventPayload(
        tenant_id="00000000-0000-0000-0000-000000000001",
        run_id="run-dlq-003",
        event_type="tool_call_end",
        agent_type="testing",
        actor_id="system:testing",
        payload={"output": "pass"},
    )

    with patch("shared.audit.service.aioredis.from_url", return_value=mock_redis_client):
        await svc.emit(payload)

    import asyncio
    await asyncio.sleep(0)

    mock_redis_client.xadd.assert_called_once()
    _, fields = mock_redis_client.xadd.call_args[0]
    import json
    recovered = json.loads(fields["payload"])
    assert recovered["run_id"] == "run-dlq-003"
    assert recovered["event_type"] == "tool_call_end"


@pytest.mark.unit
async def test_retry_worker_reemits_from_dead_letter(mock_audit_service, mock_redis_client):
    """AuditRetryWorker reads audit:dead_letter stream and calls AuditEventService.emit().

    Wave-3 implements the retry worker. This test verifies the round-trip behaviour:
    XADD payload → worker reads → AuditEventService.emit() called with correct payload.
    """
    import json
    from shared.audit.models import AuditEventPayload

    test_payload = AuditEventPayload(
        tenant_id="00000000-0000-0000-0000-000000000001",
        run_id="run-retry-001",
        event_type="tool_call_start",
        agent_type="development",
        actor_id="system:development",
        payload={"tool_name": "write_code"},
    )

    # Simulate XREADGROUP returning one message from the dead-letter stream
    stream_entry = [
        [
            b"audit:dead_letter",
            [(b"1-0", {b"payload": test_payload.model_dump_json().encode()})],
        ]
    ]
    mock_redis_client.xread = AsyncMock(return_value=stream_entry)
    mock_redis_client.xack = AsyncMock(return_value=1)

    from workers.audit_retry_worker import AuditRetryWorker

    worker = AuditRetryWorker(audit_service=mock_audit_service, redis_client=mock_redis_client)
    await worker.process_one()

    mock_audit_service.emit.assert_called_once()
    emitted = mock_audit_service.emit.call_args[0][0]
    assert emitted.run_id == "run-retry-001"
    assert emitted.event_type == "tool_call_start"
