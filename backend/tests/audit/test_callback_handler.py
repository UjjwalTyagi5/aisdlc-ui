"""REQ-M8-03 — AuditCallbackHandler unit tests.

Asserts that the LangGraph callback handler:
  - Emits tool_call_start event when on_tool_start / aon_tool_start fires
  - Emits tool_call_end event when on_tool_end / aon_tool_end fires
  - Emits model_call event when on_llm_end / aon_llm_end fires

CRITICAL: Both sync AND async variants must be tested — LangGraph astream dispatches
only the async variants (aon_tool_start, aon_tool_end, aon_llm_end). An implementation
that only overrides sync variants will silently receive no events from async agents.
(RESEARCH Pitfall 1 / RESEARCH §Assumptions Log A1)

Wave-2 turns these GREEN when shared/audit/callback_handler.py is implemented.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, call

import pytest

# Wave-2 implements shared/audit/callback_handler.py and shared/audit/models.py.
# Mark all tests xfail until that wave lands. strict=False so xpass is not a failure
# (some test environments may have the module from a partial import).
pytestmark = pytest.mark.xfail(
    reason="Wave 2 implements shared/audit/callback_handler.py",
    strict=False,
)


@pytest.mark.unit
async def test_sync_on_tool_start_emits(mock_audit_service):
    """Sync on_tool_start must emit a tool_call_start AuditEvent."""
    from shared.audit.callback_handler import AuditCallbackHandler

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-001",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )

    handler.on_tool_start(
        serialized={"name": "fetch_work_items"},
        input_str="project=Demo",
        run_id=run_id,
    )

    mock_audit_service.emit.assert_called_once()
    call_args = mock_audit_service.emit.call_args[0][0]
    assert call_args.event_type == "tool_call_start"
    assert "fetch_work_items" in str(call_args.payload)


@pytest.mark.unit
async def test_sync_on_tool_end_emits(mock_audit_service):
    """Sync on_tool_end must emit a tool_call_end AuditEvent."""
    from shared.audit.callback_handler import AuditCallbackHandler

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-001",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )

    handler.on_tool_end(
        output="[{'id': 1, 'title': 'US-001'}]",
        run_id=run_id,
    )

    mock_audit_service.emit.assert_called_once()
    call_args = mock_audit_service.emit.call_args[0][0]
    assert call_args.event_type == "tool_call_end"


@pytest.mark.unit
async def test_sync_on_llm_end_emits(mock_audit_service):
    """Sync on_llm_end must emit a model_call AuditEvent."""
    from shared.audit.callback_handler import AuditCallbackHandler

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-001",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )

    mock_response = MagicMock()
    mock_response.llm_output = {"model_name": "claude-sonnet-4-6", "token_usage": {"total": 100}}

    handler.on_llm_end(response=mock_response, run_id=run_id)

    mock_audit_service.emit.assert_called_once()
    call_args = mock_audit_service.emit.call_args[0][0]
    assert call_args.event_type == "model_call"


@pytest.mark.unit
async def test_async_aon_tool_start_emits(mock_audit_service):
    """Async aon_tool_start must emit a tool_call_start AuditEvent (CRITICAL path).

    LangGraph astream dispatches ONLY async variants — this test is the most important
    in this file. If only sync variants are implemented, this test will fail.
    (RESEARCH Pitfall 1 / Assumptions Log A1)
    """
    from shared.audit.callback_handler import AuditCallbackHandler

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-001",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )

    await handler.aon_tool_start(
        serialized={"name": "fetch_work_items"},
        input_str="project=Demo",
        run_id=run_id,
    )

    mock_audit_service.emit.assert_called()
    call_args = mock_audit_service.emit.call_args[0][0]
    assert call_args.event_type == "tool_call_start"


@pytest.mark.unit
async def test_async_aon_tool_end_emits(mock_audit_service):
    """Async aon_tool_end must emit a tool_call_end AuditEvent."""
    from shared.audit.callback_handler import AuditCallbackHandler

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-001",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )

    await handler.aon_tool_end(
        output="result string",
        run_id=run_id,
    )

    mock_audit_service.emit.assert_called()
    call_args = mock_audit_service.emit.call_args[0][0]
    assert call_args.event_type == "tool_call_end"


@pytest.mark.unit
async def test_async_aon_llm_end_emits(mock_audit_service):
    """Async aon_llm_end must emit a model_call AuditEvent."""
    from shared.audit.callback_handler import AuditCallbackHandler

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-001",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )

    mock_response = MagicMock()
    mock_response.llm_output = {"model_name": "claude-sonnet-4-6", "token_usage": {"total": 50}}

    await handler.aon_llm_end(response=mock_response, run_id=run_id)

    mock_audit_service.emit.assert_called()
    call_args = mock_audit_service.emit.call_args[0][0]
    assert call_args.event_type == "model_call"
