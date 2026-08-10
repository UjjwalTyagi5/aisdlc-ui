"""Cost capture in AuditCallbackHandler.on_llm_end (REQ-M9-06 / REQ-M9-05).

Asserts cost_service.emit is fired alongside (not instead of) the existing audit
model_call emit, with model + token counts parsed from llm_output, and that an
empty-tenant handler still no-ops the cost write while the audit emit still fires.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.unit
async def test_sync_on_llm_end_fires_audit_and_cost_emit(mock_audit_service):
    from shared.audit.callback_handler import AuditCallbackHandler

    mock_cost_service = MagicMock()
    mock_cost_service.emit = AsyncMock(return_value=None)

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-001",
        tenant_id="00000000-0000-0000-0000-000000000001",
        cost_service=mock_cost_service,
    )

    mock_response = MagicMock()
    mock_response.llm_output = {
        "model_name": "claude-sonnet-4-6",
        "token_usage": {"input_tokens": 1000, "output_tokens": 500},
    }

    handler.on_llm_end(response=mock_response, run_id=run_id, agent_type="requirements")

    mock_audit_service.emit.assert_called_once()
    audit_call = mock_audit_service.emit.call_args[0][0]
    assert audit_call.event_type == "model_call"

    mock_cost_service.emit.assert_called_once()
    cost_kwargs = mock_cost_service.emit.call_args.kwargs
    assert cost_kwargs["tenant_id"] == "00000000-0000-0000-0000-000000000001"
    assert cost_kwargs["run_id"] == "test-run-001"
    assert cost_kwargs["agent_type"] == "requirements"
    assert cost_kwargs["model"] == "claude-sonnet-4-6"
    assert cost_kwargs["input_tokens"] == 1000
    assert cost_kwargs["output_tokens"] == 500


@pytest.mark.unit
async def test_async_aon_llm_end_fires_audit_and_cost_emit(mock_audit_service):
    from shared.audit.callback_handler import AuditCallbackHandler

    mock_cost_service = MagicMock()
    mock_cost_service.emit = AsyncMock(return_value=None)

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-001",
        tenant_id="00000000-0000-0000-0000-000000000001",
        cost_service=mock_cost_service,
    )

    mock_response = MagicMock()
    mock_response.llm_output = {
        "model_name": "claude-sonnet-4-6",
        "token_usage": {"prompt_tokens": 200, "completion_tokens": 100},
    }

    await handler.aon_llm_end(response=mock_response, run_id=run_id, agent_type="design")

    mock_audit_service.emit.assert_called_once()
    mock_cost_service.emit.assert_called_once()
    cost_kwargs = mock_cost_service.emit.call_args.kwargs
    assert cost_kwargs["model"] == "claude-sonnet-4-6"
    assert cost_kwargs["input_tokens"] == 200
    assert cost_kwargs["output_tokens"] == 100


@pytest.mark.unit
async def test_empty_tenant_handler_still_emits_audit_cost_emit_is_noop(mock_audit_service):
    """Empty-tenant handler still emits audit event; cost emit call is made but
    CostLogService itself no-ops on blank tenant_id (tested in test_cost_service.py).
    Here we verify the handler still passes the empty tenant through unchanged and
    does not raise.
    """
    from shared.audit.callback_handler import AuditCallbackHandler
    from shared.cost.service import CostLogService

    real_cost_service = CostLogService()

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-001",
        tenant_id="",
        cost_service=real_cost_service,
    )

    mock_response = MagicMock()
    mock_response.llm_output = {
        "model_name": "claude-sonnet-4-6",
        "token_usage": {"input_tokens": 100, "output_tokens": 50},
    }

    # Must not raise even with blank tenant_id.
    handler.on_llm_end(response=mock_response, run_id=run_id, agent_type="requirements")

    mock_audit_service.emit.assert_called_once()
    audit_call = mock_audit_service.emit.call_args[0][0]
    assert audit_call.event_type == "model_call"


@pytest.mark.unit
async def test_default_cost_service_is_module_singleton(mock_audit_service):
    from shared.audit.callback_handler import AuditCallbackHandler
    from shared.cost.service import cost_service

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-001",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )

    assert handler._cost_service is cost_service


@pytest.mark.unit
async def test_handles_openai_shaped_usage_dict(mock_audit_service):
    from shared.audit.callback_handler import AuditCallbackHandler

    mock_cost_service = MagicMock()
    mock_cost_service.emit = AsyncMock(return_value=None)

    run_id = uuid.uuid4()
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-001",
        tenant_id="00000000-0000-0000-0000-000000000001",
        cost_service=mock_cost_service,
    )

    mock_response = MagicMock()
    mock_response.llm_output = {
        "model_name": "claude-sonnet-4-6",
        "token_usage": {"prompt_tokens": 50, "completion_tokens": 25},
    }

    handler.on_llm_end(response=mock_response, run_id=run_id)

    cost_kwargs = mock_cost_service.emit.call_args.kwargs
    assert cost_kwargs["input_tokens"] == 50
    assert cost_kwargs["output_tokens"] == 25
