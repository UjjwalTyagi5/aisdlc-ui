"""The socket hands the project's connected tools to the router on every turn.

Same shape as `test_project_documents_reach_the_turn.py`: only what needs a database
or a network is stubbed, and what is pinned is the WIRING — the block reaches
`route(connected_tools=...)`, it is read for the run's verified project, an unwired
project costs nothing, and a failed read refuses the turn.
"""
import json

import pytest

_RUN = "11111111-1111-1111-1111-111111111111"
_TICKET_USER = "u1"
_PROJECT = "44444444-4444-4444-4444-444444444444"
_BLOCK = (
    "--- CONNECTED TOOLS ON THIS PROJECT ---\n"
    "- Requirements: Confluence\n"
    "--- END CONNECTED TOOLS ON THIS PROJECT ---\n"
)


class _FakeWebSocket:
    def __init__(self, *, params=None, inbound=None):
        self.query_params = dict(params or {})
        self._inbound = list(inbound or [])
        self.sent = []

    async def accept(self):
        pass

    async def close(self, code=1000, reason=""):
        pass

    async def receive_text(self):
        from fastapi import WebSocketDisconnect
        if not self._inbound:
            raise WebSocketDisconnect()
        return self._inbound.pop(0)

    async def send_text(self, raw):
        self.sent.append(json.loads(raw))


@pytest.fixture
def ws(monkeypatch):
    from agents_orchestrator.orchestrator2 import ws as ws_module

    async def _fake_redeem(ticket):
        return {"user_id": _TICKET_USER, "tenant_id": "t1"}

    async def _fake_role(user_id, tenant_id):
        return "project_admin"

    async def _owned(run_id, tenant_id):
        return ws_module.RunSelection(model_id="m1", offering_id="o1", project_id=_PROJECT)

    async def _permissions(user_id, tenant_id):
        return ["agent:use"]

    async def _tier(project_id, tenant_id, *, user_id, permissions):
        return "project"

    async def _handoff(run_id, tenant_id, target_agent):
        return ""

    async def _documents(project_id, tenant_id):
        return ""

    async def _attachments(run_id, *, user_id):
        return ""

    async def _run_agent(agent_id, **kwargs):
        yield {"type": "stream_end"}

    monkeypatch.setattr(ws_module, "_redeem_ws_ticket", _fake_redeem)
    monkeypatch.setattr(ws_module, "_resolve_platform_role", _fake_role)
    monkeypatch.setattr(ws_module, "_resolve_run", _owned)
    monkeypatch.setattr(ws_module, "_resolve_permissions", _permissions)
    monkeypatch.setattr(ws_module, "_project_admin_tier_for_run", _tier)
    monkeypatch.setattr(ws_module, "handoff_context", _handoff)
    monkeypatch.setattr(ws_module, "approved_documents_context", _documents)
    monkeypatch.setattr(ws_module, "attachment_context", _attachments)
    monkeypatch.setattr(ws_module, "run_agent", _run_agent)
    return ws_module


def _tools(monkeypatch, ws, block):
    seen = []

    async def _fake(project_id, tenant_id):
        seen.append((project_id, tenant_id))
        return block

    monkeypatch.setattr(ws, "connected_tools_context", _fake)
    return seen


def _record_route(monkeypatch, ws):
    calls = []

    class _Decision:
        agent_id = "requirements"
        reason = "because"
        direct_reply = None

    async def _fake_route(text, **kwargs):
        calls.append(kwargs)
        return _Decision()

    monkeypatch.setattr(ws, "route", _fake_route)
    return calls


async def _serve(ws, frames):
    socket = _FakeWebSocket(params={"ticket": "tkt"}, inbound=frames)
    await ws.orchestrator2_ws(socket)
    return socket


def _frame(**kwargs):
    base = {"type": "user_message", "text": "upload the PRD to Confluence", "run_id": _RUN}
    base.update(kwargs)
    return json.dumps(base)


async def test_the_router_is_told_what_the_project_connected(ws, monkeypatch):
    _tools(monkeypatch, ws, _BLOCK)
    routes = _record_route(monkeypatch, ws)

    await _serve(ws, [_frame()])

    assert routes and routes[0]["connected_tools"] == _BLOCK


async def test_it_is_read_for_the_runs_project_not_the_frames(ws, monkeypatch):
    seen = _tools(monkeypatch, ws, "")
    _record_route(monkeypatch, ws)

    await _serve(ws, [_frame(project_id="someone-elses-project")])

    assert seen == [(_PROJECT, "t1")]


async def test_an_unwired_project_costs_nothing(ws, monkeypatch):
    _tools(monkeypatch, ws, "")
    routes = _record_route(monkeypatch, ws)

    await _serve(ws, [_frame()])

    assert routes[0]["connected_tools"] == ""


async def test_a_failed_read_refuses_the_turn(ws, monkeypatch):
    from agents_orchestrator.orchestrator2 import project_tools as pt

    async def _unavailable(project_id, tenant_id):
        raise pt.ProjectToolsUnavailableError("database is on fire")

    monkeypatch.setattr(ws, "connected_tools_context", _unavailable)
    routes = _record_route(monkeypatch, ws)

    socket = await _serve(ws, [_frame()])

    assert not routes, "the router must not decide on a roster it knows is incomplete"
    types = [e["type"] for e in socket.sent]
    assert "error" in types and types[-1] == "stream_end"
