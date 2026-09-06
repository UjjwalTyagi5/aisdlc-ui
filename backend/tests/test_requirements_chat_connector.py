"""The Requirements chat handlers (WS + REST) must enter agent_run_scope so the
tenant connector is live for the graph run (live-pass finding F1).

These tests stub the graph stream and the scope's connector resolution, then assert
a connector is live inside the stream and released afterward.

chat() now requires a verified `request` (identity comes from request.state, never
a trusted form field — see its docstring) and both handlers call
assert_agent_access_for_chat against a real project. Neither concern is what this
test is about, so both are stubbed out here the same way test_agent_run_scope.py
stubs _stage_board_kind: this is a connector-injection unit test, not an access- or
project-resolution integration test.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import agents_orchestrator.requirements_agent.requirements_agent_api as api
from config.connectors.context import get_connector

_TENANT_ID = str(uuid.uuid4())


async def _fake_access_check(db, *, tenant_id, project_id, user_id, agent_id):
    """Bypass the real project/membership DB check — no project exists in this test."""
    return None


async def _fake_stage_kind(tenant_id, project_id, agent_id, owner_id=None):
    # owner_id: which of the stage's boards this PERSON can authenticate against —
    # the real one probes credentials so a stage with two boards cannot resolve to
    # one nobody connected. Irrelevant to this test, which pins the scope wiring.
    return "azure_devops"


@pytest.mark.asyncio
async def test_rest_chat_enters_scope_with_connector(monkeypatch, tmp_path):
    mock_connector = MagicMock()
    mock_connector.display_name = "Azure DevOps"

    async def _fake_get(kind="azure_devops", tenant_id="", **kwargs):
        return mock_connector

    # Resolve the connector inside the real agent_run_scope.
    monkeypatch.setattr("shared.services.agent_run.get_connector_for_session", _fake_get)
    monkeypatch.setattr("shared.services.agent_run._stage_board_kind", _fake_stage_kind)
    monkeypatch.setattr(api, "assert_agent_access_for_chat", _fake_access_check)

    connector_seen = {}

    async def _fake_astream(state, stream_mode=None, config=None):
        # Inside the graph run the connector must be live (this is what board tools see).
        try:
            connector_seen["value"] = get_connector()
        except RuntimeError:
            connector_seen["value"] = None
        if False:
            yield  # make this an async generator
        return

    monkeypatch.setattr(api.planning_app, "astream", _fake_astream)
    monkeypatch.setattr(api.shared, "output_file", "", raising=False)
    monkeypatch.setattr(api.esett, "FILES", str(tmp_path), raising=False)
    # Avoid any real persistence call.
    async def _noop_persist(**kwargs):
        return None
    monkeypatch.setattr(api, "_persist_session_artifacts", _noop_persist)

    # Fresh session so first_turn branch runs.
    api._initialized_sessions.discard("sess-rest-1")

    fake_request = SimpleNamespace(state=SimpleNamespace(user_id="user-1", tenant_id=_TENANT_ID))

    await api.chat(
        request=fake_request,
        conversation_context="pull my ADO work items",
        task_intent="",
        pipeline_context=None,
        provider_kind="azure_devops",
        session_id="sess-rest-1",
        user_id="user-1",
        tenant_id=_TENANT_ID,
        model_id=None,
        uploaded_files=None,
        db=AsyncMock(),
    )

    assert connector_seen["value"] is mock_connector
    # Released after the handler returns (REQ-M3-10).
    with pytest.raises(RuntimeError):
        get_connector()


@pytest.mark.asyncio
async def test_ws_chat_enters_scope_with_connector(monkeypatch, tmp_path):
    mock_connector = MagicMock()
    mock_connector.display_name = "Azure DevOps"

    async def _fake_get(kind="azure_devops", tenant_id="", **kwargs):
        return mock_connector

    monkeypatch.setattr("shared.services.agent_run.get_connector_for_session", _fake_get)
    monkeypatch.setattr("shared.services.agent_run._stage_board_kind", _fake_stage_kind)
    monkeypatch.setattr(api, "assert_agent_access_for_chat", _fake_access_check)

    connector_seen = {}

    async def _fake_stream_agent_response(state, config, websocket, session_id):
        try:
            connector_seen["value"] = get_connector()
        except RuntimeError:
            connector_seen["value"] = None
        return "ok"

    monkeypatch.setattr(api, "_stream_agent_response", _fake_stream_agent_response)
    monkeypatch.setattr(api.esett, "FILES", str(tmp_path), raising=False)

    class _State:
        values = {"messages": []}

    async def _fake_get_state(config):
        return _State()

    monkeypatch.setattr(api.planning_app, "aget_state", _fake_get_state)

    class _FakeManager:
        async def broadcast(self, *a, **k):
            return None
        async def send_personal_message(self, *a, **k):
            return None
        async def send_agent_response(self, *a, **k):
            return None
        async def send_file_processing_update(self, *a, **k):
            return None

    monkeypatch.setattr(api, "manager", _FakeManager())

    api._initialized_sessions.discard("sess-ws-1")

    await api._process_user_message_ws(
        {"session_id": "sess-ws-1", "conversation_context": "pull my ADO work items"},
        websocket=MagicMock(),
        user_id="user-1",
        tenant_id=_TENANT_ID,
    )

    assert connector_seen["value"] is mock_connector
    with pytest.raises(RuntimeError):
        get_connector()
