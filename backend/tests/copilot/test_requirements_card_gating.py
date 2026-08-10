"""FIX (c): `_maybe_emit_choice_card`'s requirements branch must not re-emit a stale
"Select project/stories" card once the agent has already acted on a selection this
run — either the requirements payload is already packaged (persisted on the run row)
or this turn is a HANDOFF (about to be persisted). It should still emit the card on
the genuine "agent listed items and is asking the user to pick" turn."""
from types import SimpleNamespace

import pytest

from agents_orchestrator.orchestrator import copilot_api


class _FakeAsyncCM:
    """Stand-in for `async with get_db_session_superuser() as s:` — mirrors the
    pattern used in tests/copilot/test_capture_stage_files.py."""

    def __init__(self, run):
        self._run = run

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, *args, **kwargs):
        return SimpleNamespace(scalar_one_or_none=lambda: self._run)


class _FakeWebSocket:
    async def send_text(self, text):
        pass


def _patch_run(monkeypatch, requirements_payload):
    run = SimpleNamespace(requirements_payload=requirements_payload)
    monkeypatch.setattr(copilot_api, "get_db_session_superuser", lambda: _FakeAsyncCM(run))


@pytest.mark.asyncio
async def test_no_card_when_requirements_payload_already_packaged(monkeypatch):
    _patch_run(monkeypatch, {"title": "already packaged"})

    async def _fail_last_list_tool(graph, run_id):
        raise AssertionError("_last_list_tool should not be reached once payload is packaged")

    sent = []

    async def _fake_send(websocket, payload):
        sent.append(payload)

    monkeypatch.setattr(copilot_api, "_last_list_tool", _fail_last_list_tool)
    monkeypatch.setattr(copilot_api, "_send", _fake_send)

    await copilot_api._maybe_emit_choice_card(
        "requirements", graph=object(), run_id="run-1", tenant_id="t1",
        project_id="p1", shim_input={}, websocket=_FakeWebSocket())

    assert sent == []


@pytest.mark.asyncio
async def test_no_card_when_handoff_detected_this_turn(monkeypatch):
    _patch_run(monkeypatch, None)

    async def _fail_last_list_tool(graph, run_id):
        raise AssertionError("_last_list_tool should not be reached once a handoff fired")

    async def _fake_detect_handoff(graph, run_id):
        return {"to": "design", "stage_completed": "requirements"}

    sent = []

    async def _fake_send(websocket, payload):
        sent.append(payload)

    monkeypatch.setattr(copilot_api, "_last_list_tool", _fail_last_list_tool)
    monkeypatch.setattr(copilot_api, "_detect_handoff", _fake_detect_handoff)
    monkeypatch.setattr(copilot_api, "_send", _fake_send)

    await copilot_api._maybe_emit_choice_card(
        "requirements", graph=object(), run_id="run-2", tenant_id="t1",
        project_id="p1", shim_input={}, websocket=_FakeWebSocket())

    assert sent == []


@pytest.mark.asyncio
async def test_still_emits_when_payload_null_and_list_tool_called(monkeypatch):
    _patch_run(monkeypatch, None)

    async def _fake_detect_handoff(graph, run_id):
        return None

    async def _fake_last_list_tool(graph, run_id):
        return "list_board_projects"

    class _FakeConnector:
        async def read_adapter(self, name, **kwargs):
            assert name == "list_projects"
            return [{"id": "p1", "name": "Payments"}]

    async def _fake_get_connector_for_session(kind, tenant_id):
        return _FakeConnector()

    monkeypatch.setattr(copilot_api, "_detect_handoff", _fake_detect_handoff)
    monkeypatch.setattr(copilot_api, "_last_list_tool", _fake_last_list_tool)

    import config.connector_factory as connector_factory
    import config.connectors.context as connector_context
    import workflows.activities._base as workflow_base

    monkeypatch.setattr(connector_factory, "get_connector_for_session",
                         _fake_get_connector_for_session)
    monkeypatch.setattr(connector_context, "set_connector", lambda c: None)
    monkeypatch.setattr(connector_context, "clear_connector", lambda: None)
    monkeypatch.setattr(workflow_base, "stage_connector_kind", lambda shim, stage: "azure_devops")

    sent = []

    async def _fake_send(websocket, payload):
        sent.append(payload)

    monkeypatch.setattr(copilot_api, "_send", _fake_send)

    await copilot_api._maybe_emit_choice_card(
        "requirements", graph=object(), run_id="run-3", tenant_id="t1",
        project_id="p1", shim_input={}, websocket=_FakeWebSocket())

    assert len(sent) == 1
    assert sent[0]["type"] == "choice.card"
    assert [o["label"] for o in sent[0]["card"]["options"]] == ["Payments"]
