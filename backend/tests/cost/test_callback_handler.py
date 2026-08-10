"""Focused async audit+cost-emit tests for AuditCallbackHandler.

Verifies that:
  - aon_llm_end awaits both _svc.emit (audit) and cost_service.emit exactly once
  - aon_tool_start / aon_tool_end await _svc.emit (audit) exactly once each
  - on_llm_end (sync) does not raise even with async emits (create_task / close path)
  - on_tool_start / on_tool_end (sync) do not raise
  - No "coroutine never awaited" RuntimeWarning is emitted from any path
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.unit
async def test_aon_llm_end_awaits_cost_emit_exactly_once(mock_audit_service):
    """aon_llm_end must await cost_service.emit exactly once with correct token counts.

    This is the primary regression test for the async cost-emit bug: before the fix,
    aon_llm_end delegated to on_llm_end which called cost_service.emit() without
    await, creating a coroutine that was never awaited (RuntimeWarning, no DB write).
    """
    from shared.audit.callback_handler import AuditCallbackHandler

    mock_cost_service = MagicMock()
    mock_cost_service.emit = AsyncMock(return_value=None)

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-async-fix",
        tenant_id="00000000-0000-0000-0000-000000000002",
        cost_service=mock_cost_service,
    )

    mock_response = MagicMock()
    mock_response.llm_output = {
        "model_name": "claude-sonnet-4-6",
        "token_usage": {"input_tokens": 750, "output_tokens": 250},
    }

    await handler.aon_llm_end(response=mock_response, run_id=run_id, agent_type="design")

    mock_cost_service.emit.assert_awaited_once()
    cost_kwargs = mock_cost_service.emit.call_args.kwargs
    assert cost_kwargs["tenant_id"] == "00000000-0000-0000-0000-000000000002"
    assert cost_kwargs["run_id"] == "test-run-async-fix"
    assert cost_kwargs["agent_type"] == "design"
    assert cost_kwargs["model"] == "claude-sonnet-4-6"
    assert cost_kwargs["input_tokens"] == 750
    assert cost_kwargs["output_tokens"] == 250


@pytest.mark.unit
async def test_aon_llm_end_awaits_audit_emit_exactly_once(mock_audit_service):
    """aon_llm_end must await _svc.emit (audit) exactly once with model_call event.

    Before the async-audit fix, _record_llm_audit_and_metrics called self._svc.emit()
    synchronously, creating a coroutine that was never awaited (RuntimeWarning, no
    audit_events row written).  aon_llm_end now awaits the audit emit directly.
    """
    from shared.audit.callback_handler import AuditCallbackHandler

    mock_cost_service = MagicMock()
    mock_cost_service.emit = AsyncMock(return_value=None)

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-audit-fix",
        tenant_id="00000000-0000-0000-0000-000000000005",
        cost_service=mock_cost_service,
    )

    mock_response = MagicMock()
    mock_response.llm_output = {
        "model_name": "claude-sonnet-4-6",
        "token_usage": {"input_tokens": 100, "output_tokens": 50},
    }

    await handler.aon_llm_end(response=mock_response, run_id=run_id, agent_type="requirements")

    mock_audit_service.emit.assert_awaited_once()
    audit_call = mock_audit_service.emit.call_args[0][0]
    assert audit_call.event_type == "model_call"
    assert audit_call.agent_type == "requirements"
    assert audit_call.run_id == "test-run-audit-fix"


@pytest.mark.unit
async def test_aon_tool_start_awaits_audit_emit(mock_audit_service):
    """aon_tool_start must await _svc.emit with tool_call_start event."""
    from shared.audit.callback_handler import AuditCallbackHandler

    mock_cost_service = MagicMock()
    mock_cost_service.emit = AsyncMock(return_value=None)

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-tool-start",
        tenant_id="00000000-0000-0000-0000-000000000006",
        cost_service=mock_cost_service,
    )

    serialized = {"name": "list_work_items"}
    await handler.aon_tool_start(
        serialized, "some input", run_id=run_id, agent_type="requirements"
    )

    mock_audit_service.emit.assert_awaited_once()
    audit_call = mock_audit_service.emit.call_args[0][0]
    assert audit_call.event_type == "tool_call_start"
    assert audit_call.agent_type == "requirements"


@pytest.mark.unit
async def test_aon_tool_end_awaits_audit_emit(mock_audit_service):
    """aon_tool_end must await _svc.emit with tool_call_end event."""
    from shared.audit.callback_handler import AuditCallbackHandler

    mock_cost_service = MagicMock()
    mock_cost_service.emit = AsyncMock(return_value=None)

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-tool-end",
        tenant_id="00000000-0000-0000-0000-000000000007",
        cost_service=mock_cost_service,
    )

    # Pre-populate pending so on_tool_end finds it
    handler._pending_tool[str(run_id)] = {
        "tool_name": "list_work_items",
        "input": "query",
        "_t0": 0.0,
    }

    await handler.aon_tool_end("some output", run_id=run_id, agent_type="requirements")

    mock_audit_service.emit.assert_awaited_once()
    audit_call = mock_audit_service.emit.call_args[0][0]
    assert audit_call.event_type == "tool_call_end"
    assert audit_call.agent_type == "requirements"


@pytest.mark.unit
async def test_on_llm_end_sync_does_not_raise(mock_audit_service):
    """Sync on_llm_end must not raise when both _svc.emit and cost_service.emit are async.

    In a running-loop context (async test), both coroutines are scheduled via
    create_task.  Either way, the function must return without raising.
    """
    from shared.audit.callback_handler import AuditCallbackHandler

    mock_cost_service = MagicMock()
    mock_cost_service.emit = AsyncMock(return_value=None)

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-sync-noerror",
        tenant_id="00000000-0000-0000-0000-000000000003",
        cost_service=mock_cost_service,
    )

    mock_response = MagicMock()
    mock_response.llm_output = {
        "model_name": "claude-sonnet-4-6",
        "token_usage": {"input_tokens": 100, "output_tokens": 50},
    }

    # Must not raise
    handler.on_llm_end(response=mock_response, run_id=run_id, agent_type="requirements")


@pytest.mark.unit
async def test_on_tool_start_sync_does_not_raise(mock_audit_service):
    """Sync on_tool_start must not raise when _svc.emit is async."""
    from shared.audit.callback_handler import AuditCallbackHandler

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-tool-start-sync",
        tenant_id="00000000-0000-0000-0000-000000000008",
    )

    handler.on_tool_start({"name": "some_tool"}, "input", run_id=run_id, agent_type="agent")


@pytest.mark.unit
async def test_on_tool_end_sync_does_not_raise(mock_audit_service):
    """Sync on_tool_end must not raise when _svc.emit is async."""
    from shared.audit.callback_handler import AuditCallbackHandler

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-tool-end-sync",
        tenant_id="00000000-0000-0000-0000-000000000009",
    )

    handler.on_tool_end("output", run_id=run_id, agent_type="agent")


@pytest.mark.unit
async def test_aon_llm_end_emits_audit_before_cost(mock_audit_service):
    """Audit emit must be awaited before cost emit is awaited in aon_llm_end."""
    from shared.audit.callback_handler import AuditCallbackHandler

    call_order = []

    async def _audit_emit(payload):
        call_order.append("audit")

    async def _cost_emit(**kw):
        call_order.append("cost")

    mock_audit_service.emit = _audit_emit

    mock_cost_service = MagicMock()
    mock_cost_service.emit = _cost_emit

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-order",
        tenant_id="00000000-0000-0000-0000-000000000004",
        cost_service=mock_cost_service,
    )

    mock_response = MagicMock()
    mock_response.llm_output = {
        "model_name": "claude-sonnet-4-6",
        "token_usage": {"input_tokens": 10, "output_tokens": 5},
    }

    await handler.aon_llm_end(response=mock_response, run_id=run_id, agent_type="agent")

    assert call_order == ["audit", "cost"], f"unexpected order: {call_order}"
