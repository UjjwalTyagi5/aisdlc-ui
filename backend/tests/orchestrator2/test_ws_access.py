"""The Orchestrator socket's access check and turn contract.

The first two tests are the brief's source-level guards. Everything after them
drives the real handler with a fake WebSocket, because a `grep` for the string
"project_admin" proves only that the string is present — the Phase 1 review found
a per-project Orchestrator route that was gated in the nav, gated in the global
route, gated on the button, and completely open to anyone who typed the URL. The
behavioural tests below are what actually prove the socket refuses.
"""
import inspect
import json

import pytest
from fastapi import WebSocketDisconnect


def test_socket_enforces_project_admin_server_side():
    """UI gating is not access control. The Phase 1 review found the per-project
    Orchestrator route completely ungated — a URL was enough. The socket must
    check the role itself."""
    from agents_orchestrator.orchestrator2 import ws
    src = inspect.getsource(ws)
    assert "project_admin" in src


def test_socket_has_no_gate_or_progression_machinery():
    from agents_orchestrator.orchestrator2 import ws
    src = inspect.getsource(ws)
    for banned in ("gate.state", "gate.decision", "next_stage", "HANDOFF::", "auto_advance"):
        assert banned not in src, f"{banned} must not appear in the Orchestrator socket"


# ── behavioural harness ──────────────────────────────────────────────────────


class _FakeWebSocket:
    """Enough of starlette's WebSocket for the handler: query params, the accept
    /close handshake, and a scripted inbound queue that ends the turn loop with
    WebSocketDisconnect exactly as a real client hang-up does."""

    def __init__(self, *, params=None, inbound=None):
        self.query_params = dict(params or {})
        self._inbound = list(inbound or [])
        self.accepted = False
        self.closed = None  # (code, reason)
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

    async def receive_text(self):
        if not self._inbound:
            raise WebSocketDisconnect()
        return self._inbound.pop(0)

    async def send_text(self, raw):
        self.sent.append(json.loads(raw))

    @property
    def events(self):
        return self.sent


def _patch_auth(monkeypatch, ws, *, role, claims=None):
    """Redeem a ticket into fixed claims and resolve a fixed platform role."""
    async def _fake_redeem(ticket):
        return claims if claims is not None else {"user_id": "u1", "tenant_id": "t1"}

    async def _fake_role(user_id, tenant_id):
        return role

    monkeypatch.setattr(ws, "_redeem_ws_ticket", _fake_redeem)
    monkeypatch.setattr(ws, "_resolve_platform_role", _fake_role)

    async def _no_db(run_id):
        return None, None

    monkeypatch.setattr(ws, "_run_model_offering", _no_db)


def _record_run_agent(monkeypatch, ws, events):
    """Replace run_agent with a recorder yielding `events`."""
    calls = []

    async def _fake_run_agent(agent_id, **kwargs):
        calls.append((agent_id, kwargs))
        for event in events:
            yield event

    monkeypatch.setattr(ws, "run_agent", _fake_run_agent)
    return calls


# ── the access check ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["developer", "contributor", "tester", None])
async def test_delivery_and_roleless_callers_are_refused_before_any_turn(monkeypatch, role):
    """A URL was enough last time. A delivery role reaching this socket directly
    must never get a turn — the Orchestrator reaches all nine agents at once, so
    driving it IS holding every agent's access."""
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws, role=role)
    calls = _record_run_agent(monkeypatch, ws, [{"type": "stream_end"}])

    socket = _FakeWebSocket(
        params={"ticket": "tkt"},
        inbound=[json.dumps({"type": "user_message", "text": "hi", "agent": "design",
                             "run_id": "r1", "project_id": "p1"})],
    )
    await ws.orchestrator2_ws(socket)

    assert socket.accepted is False, "a refused caller must never get an accepted socket"
    assert socket.closed is not None and socket.closed[0] == 4403
    assert calls == [], "no agent may run for a refused caller"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["org_admin", "bu_admin"])
async def test_governance_tier_is_refused_too(monkeypatch, role):
    """org_admin and bu_admin outrank Project Admin everywhere else and still hold
    no agent access: they decide who may run agents, they do not run them."""
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws, role=role)
    calls = _record_run_agent(monkeypatch, ws, [{"type": "stream_end"}])

    socket = _FakeWebSocket(params={"ticket": "tkt"}, inbound=[])
    await ws.orchestrator2_ws(socket)

    assert socket.accepted is False
    assert socket.closed is not None and socket.closed[0] == 4403
    assert calls == []


@pytest.mark.asyncio
async def test_project_admin_is_admitted(monkeypatch):
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws, role="project_admin")
    _record_run_agent(monkeypatch, ws, [{"type": "stream_end"}])

    socket = _FakeWebSocket(params={"ticket": "tkt"}, inbound=[])
    await ws.orchestrator2_ws(socket)

    assert socket.accepted is True
    assert socket.closed is None or socket.closed[0] != 4403


@pytest.mark.asyncio
async def test_missing_or_invalid_ticket_is_refused(monkeypatch):
    from agents_orchestrator.orchestrator2 import ws

    async def _reject(ticket):
        return None

    monkeypatch.setattr(ws, "_redeem_ws_ticket", _reject)
    calls = _record_run_agent(monkeypatch, ws, [{"type": "stream_end"}])

    for params in ({}, {"ticket": "expired"}):
        socket = _FakeWebSocket(params=params, inbound=[])
        await ws.orchestrator2_ws(socket)
        assert socket.accepted is False
        assert socket.closed is not None and socket.closed[0] == 4401
    assert calls == []


@pytest.mark.asyncio
async def test_role_is_resolved_from_the_ticket_claims_not_from_the_client(monkeypatch):
    """The caller supplies a ticket, never a role. The role is resolved server-side
    from the redeemed claims, so a hand-rolled client cannot assert one."""
    from agents_orchestrator.orchestrator2 import ws
    seen = []

    async def _fake_redeem(ticket):
        return {"user_id": "u-42", "tenant_id": "t-9"}

    async def _fake_role(user_id, tenant_id):
        seen.append((user_id, tenant_id))
        return "project_admin"

    monkeypatch.setattr(ws, "_redeem_ws_ticket", _fake_redeem)
    monkeypatch.setattr(ws, "_resolve_platform_role", _fake_role)

    socket = _FakeWebSocket(
        params={"ticket": "tkt"},
        # A client claiming to be a Project Admin in the URL and in the payload.
        inbound=[json.dumps({"type": "user_message", "role": "project_admin"})],
    )
    await ws.orchestrator2_ws(socket)

    assert seen == [("u-42", "t-9")]


# ── the turn contract ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_named_agent_is_dispatched_and_its_events_stream_out_verbatim(monkeypatch):
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws, role="project_admin")
    emitted = [
        {"type": "agent.selected", "agent": "design", "reason": "", "run_id": "r1"},
        {"type": "stream_chunk", "content": "Hello"},
        {"type": "stream_end"},
    ]
    calls = _record_run_agent(monkeypatch, ws, emitted)

    socket = _FakeWebSocket(
        params={"ticket": "tkt"},
        inbound=[json.dumps({"type": "user_message", "text": "hi", "agent": "design",
                             "run_id": "r1", "project_id": "p1"})],
    )
    await ws.orchestrator2_ws(socket)

    assert len(calls) == 1
    agent_id, kwargs = calls[0]
    assert agent_id == "design"
    assert kwargs["text"] == "hi"
    assert kwargs["run_id"] == "r1"
    assert kwargs["tenant_id"] == "t1"
    assert socket.events == emitted, "events must be forwarded unchanged, not re-wrapped"


@pytest.mark.asyncio
async def test_a_message_with_no_agent_is_an_error_never_a_default(monkeypatch):
    """Phase 2 dispatches only an explicitly named agent. A silent default is
    exactly how the old engine hid six missing prompts."""
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws, role="project_admin")
    calls = _record_run_agent(monkeypatch, ws, [{"type": "stream_end"}])

    socket = _FakeWebSocket(
        params={"ticket": "tkt"},
        inbound=[json.dumps({"type": "user_message", "text": "hi",
                             "run_id": "r1", "project_id": "p1"})],
    )
    await ws.orchestrator2_ws(socket)

    assert calls == [], "no agent may be guessed"
    errors = [e for e in socket.events if e["type"] == "error"]
    assert errors and "agent" in (errors[0].get("message") or "")
    assert socket.events[-1]["type"] == "stream_end", "the turn must still terminate"


@pytest.mark.asyncio
async def test_malformed_and_unsupported_frames_produce_errors_not_silence(monkeypatch):
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws, role="project_admin")
    _record_run_agent(monkeypatch, ws, [{"type": "stream_end"}])

    socket = _FakeWebSocket(
        params={"ticket": "tkt"},
        inbound=["{not json", json.dumps({"type": "gate_decision"})],
    )
    await ws.orchestrator2_ws(socket)

    assert [e["type"] for e in socket.events].count("error") == 2
    assert socket.events[-1]["type"] == "stream_end"


@pytest.mark.asyncio
async def test_an_exception_in_the_turn_becomes_an_error_event_not_a_dead_socket(monkeypatch):
    """An exception must never kill the socket silently — that is the failure mode
    this whole phase exists to remove."""
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws, role="project_admin")

    def _explode(agent_id, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(ws, "run_agent", _explode)

    socket = _FakeWebSocket(
        params={"ticket": "tkt"},
        inbound=[
            json.dumps({"type": "user_message", "text": "hi", "agent": "design",
                        "run_id": "r1", "project_id": "p1"}),
            json.dumps({"type": "user_message", "text": "again", "agent": "design",
                        "run_id": "r1", "project_id": "p1"}),
        ],
    )
    await ws.orchestrator2_ws(socket)

    errors = [e for e in socket.events if e["type"] == "error"]
    assert len(errors) == 2, "the socket must survive a failing turn and keep serving"
    assert "boom" in (errors[0].get("detail") or errors[0].get("message") or "")


@pytest.mark.asyncio
async def test_every_emitted_event_type_is_in_the_frontend_union(monkeypatch):
    """A type string differing even slightly means the browser's Zod union silently
    drops the frame (frontend/lib/orchestrator/protocol.ts)."""
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws, role="project_admin")
    _record_run_agent(monkeypatch, ws, [{"type": "stream_end"}])

    valid = {"stream_chunk", "stream_end", "agent.selected", "tool.call",
             "agent.thinking", "choice.card", "error"}
    socket = _FakeWebSocket(
        params={"ticket": "tkt"},
        inbound=["{not json", json.dumps({"type": "user_message", "text": "hi"})],
    )
    await ws.orchestrator2_ws(socket)

    assert socket.events, "expected the socket to say something"
    for event in socket.events:
        assert event["type"] in valid, f"unexpected event type: {event['type']!r}"


@pytest.mark.asyncio
async def test_router_is_mounted_at_the_ws_path():
    from agents_orchestrator.orchestrator2.ws import orchestrator2_router
    paths = [getattr(r, "path", "") for r in orchestrator2_router.routes]
    assert "/ws" in paths
