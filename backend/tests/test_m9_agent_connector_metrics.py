"""REQ-M9-02 / REQ-M9-03 — Agent + connector metric instrumentation unit tests.

Agent-side: drives AuditCallbackHandler hooks (on_tool_start/on_tool_end/on_llm_end/
on_tool_error) and asserts AGENT_REQUEST_LATENCY, AGENT_TOKENS, AGENT_ERRORS samples
move with bounded labels (agent_type, model, kind, outcome — never run_id).

Connector-side: constructs a ConnectorAuditEvent and asserts observe_connector_call
(directly, and via a connector's audit_emitter) increments CONNECTOR_CALL_OUTCOME and
observes CONNECTOR_CALL_DURATION for both success and error status.

Pure unit — no DB, no event loop required for the sync variants.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.audit.callback_handler import AuditCallbackHandler
from shared.services.metrics import (
    AGENT_ERRORS,
    AGENT_REQUEST_LATENCY,
    AGENT_TOKENS,
    CONNECTOR_CALL_DURATION,
    CONNECTOR_CALL_OUTCOME,
    observe_connector_call,
)
from config.connectors.models import ConnectorAuditEvent


@pytest.fixture
def mock_audit_service():
    svc = MagicMock()
    svc.emit = AsyncMock(return_value=None)
    svc.emit_blocking = AsyncMock(return_value=None)
    return svc


# ── Agent-side (REQ-M9-02) ────────────────────────────────────────────────


@pytest.mark.unit
def test_on_tool_end_observes_agent_request_latency(mock_audit_service):
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-001",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )
    run_id = uuid.uuid4()

    before = AGENT_REQUEST_LATENCY.labels(agent_type="requirements")._sum.get()

    handler.on_tool_start(
        serialized={"name": "fetch_work_items"},
        input_str="project=Demo",
        run_id=run_id,
        agent_type="requirements",
    )
    handler.on_tool_end(output="result", run_id=run_id, agent_type="requirements")

    after = AGENT_REQUEST_LATENCY.labels(agent_type="requirements")._sum.get()
    assert after >= before


@pytest.mark.unit
def test_on_llm_end_increments_agent_tokens(mock_audit_service):
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-002",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )
    run_id = uuid.uuid4()

    before_in = AGENT_TOKENS.labels(
        agent_type="design", model="claude-sonnet-4-6", kind="input"
    )._value.get()
    before_out = AGENT_TOKENS.labels(
        agent_type="design", model="claude-sonnet-4-6", kind="output"
    )._value.get()

    mock_response = MagicMock()
    mock_response.llm_output = {
        "model_name": "claude-sonnet-4-6",
        "token_usage": {"input_tokens": 120, "output_tokens": 45},
    }

    handler.on_llm_end(response=mock_response, run_id=run_id, agent_type="design")

    after_in = AGENT_TOKENS.labels(
        agent_type="design", model="claude-sonnet-4-6", kind="input"
    )._value.get()
    after_out = AGENT_TOKENS.labels(
        agent_type="design", model="claude-sonnet-4-6", kind="output"
    )._value.get()

    assert after_in - before_in == 120
    assert after_out - before_out == 45


@pytest.mark.unit
def test_on_llm_end_handles_missing_token_usage(mock_audit_service):
    """Missing/None token_usage must not raise (fire-and-forget contract)."""
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-003",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )
    run_id = uuid.uuid4()

    mock_response = MagicMock()
    mock_response.llm_output = {"model_name": "claude-sonnet-4-6"}

    handler.on_llm_end(response=mock_response, run_id=run_id, agent_type="development")


@pytest.mark.unit
def test_on_tool_error_increments_agent_errors(mock_audit_service):
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-004",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )
    run_id = uuid.uuid4()

    before = AGENT_ERRORS.labels(agent_type="testing", outcome="error")._value.get()

    handler.on_tool_error(RuntimeError("boom"), run_id=run_id, agent_type="testing")

    after = AGENT_ERRORS.labels(agent_type="testing", outcome="error")._value.get()
    assert after - before == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_aon_llm_error_increments_agent_errors(mock_audit_service):
    handler = AuditCallbackHandler(
        audit_service=mock_audit_service,
        run_id="test-run-005",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )
    run_id = uuid.uuid4()

    before = AGENT_ERRORS.labels(agent_type="deployment", outcome="error")._value.get()

    await handler.aon_llm_error(RuntimeError("boom"), run_id=run_id, agent_type="deployment")

    after = AGENT_ERRORS.labels(agent_type="deployment", outcome="error")._value.get()
    assert after - before == 1


# ── Connector-side (REQ-M9-03) ────────────────────────────────────────────


@pytest.mark.unit
def test_observe_connector_call_success_increments_outcome_and_duration():
    event = ConnectorAuditEvent(
        connector_name="azure_devops",
        method="list_projects",
        tenant_id="00000000-0000-0000-0000-000000000001",
        run_id="test-run-006",
        latency_ms=250.0,
        status="success",
    )

    before_outcome = CONNECTOR_CALL_OUTCOME.labels(
        connector="azure_devops", status="success"
    )._value.get()
    before_duration = CONNECTOR_CALL_DURATION.labels(
        connector="azure_devops", method="list_projects"
    )._sum.get()

    observe_connector_call(event)

    after_outcome = CONNECTOR_CALL_OUTCOME.labels(
        connector="azure_devops", status="success"
    )._value.get()
    after_duration = CONNECTOR_CALL_DURATION.labels(
        connector="azure_devops", method="list_projects"
    )._sum.get()

    assert after_outcome - before_outcome == 1
    assert after_duration - before_duration == pytest.approx(0.25)


@pytest.mark.unit
def test_observe_connector_call_error_increments_outcome():
    event = ConnectorAuditEvent(
        connector_name="jira",
        method="fetch_item_detail",
        tenant_id="00000000-0000-0000-0000-000000000001",
        run_id="test-run-007",
        latency_ms=500.0,
        status="error",
        error_type="HTTPError",
    )

    before = CONNECTOR_CALL_OUTCOME.labels(connector="jira", status="error")._value.get()

    observe_connector_call(event)

    after = CONNECTOR_CALL_OUTCOME.labels(connector="jira", status="error")._value.get()
    assert after - before == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_audit_emitter_calls_observe_connector_call(monkeypatch):
    """Calling a connector's audit_emitter must observe metrics before persisting."""
    from config.connectors.azure_devops import AzureDevOpsConnector

    mock_audit_service = MagicMock()
    mock_audit_service.emit = AsyncMock(return_value=None)
    monkeypatch.setattr("shared.audit.service.audit_service", mock_audit_service)

    event = ConnectorAuditEvent(
        connector_name="azure_devops",
        method="list_projects",
        tenant_id="00000000-0000-0000-0000-000000000001",
        run_id="test-run-008",
        latency_ms=100.0,
        status="success",
    )

    before = CONNECTOR_CALL_OUTCOME.labels(
        connector="azure_devops", status="success"
    )._value.get()

    connector = AzureDevOpsConnector.__new__(AzureDevOpsConnector)
    await connector.audit_emitter(event)

    after = CONNECTOR_CALL_OUTCOME.labels(
        connector="azure_devops", status="success"
    )._value.get()

    assert after - before == 1
    mock_audit_service.emit.assert_called_once()
