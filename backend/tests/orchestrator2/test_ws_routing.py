"""The socket routes by conversation, and an explicit choice still wins.

Phase 2 refused a frame that named no agent, on purpose: a silent default is how the
old engine dispatched the wrong agent invisibly. Phase 3 replaces that refusal with a
routing decision the user can see — `agent.selected` carries the reason every time —
and keeps the explicit `agent` field as an OVERRIDE, because the router is a model and
a wrong decision with no way to force an agent would be unrecoverable inside the
conversation.

What is asserted here, beyond the happy path:

  * routing NEVER runs for a run the caller does not own — an unowned run must not be
    able to spend a model call;
  * the router is called with the run's `project_id`, from the verified `runs` row.
    Dropping it would reintroduce the BYOK gap Phase 2 closed, on a new code path:
    `project_id` is what decides which models a turn may use and whose budget it
    spends;
  * a decision to answer directly loads NO graph;
  * exactly one `agent.selected` reaches the wire, carrying the router's reason —
    `run_agent` emits its own, and two of them would leave the second erasing the
    first's explanation.
"""
import json

import pytest

from agents_orchestrator.orchestrator2 import router as rtr


_RUN = "11111111-1111-1111-1111-111111111111"


class _FakeWebSocket:
    def __init__(self, *, params=None, inbound=None):
        self.query_params = dict(params or {})
        self._inbound = list(inbound or [])
        self.accepted = False
        self.closed = None
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

    async def receive_text(self):
        from fastapi import WebSocketDisconnect
        if not self._inbound:
            raise WebSocketDisconnect()
        return self._inbound.pop(0)

    async def send_text(self, raw):
        self.sent.append(json.loads(raw))


def _patch_auth(monkeypatch, ws, *, project_id="proj-A"):
    async def _fake_redeem(ticket):
        return {"user_id": "u1", "tenant_id": "t1"}

    async def _fake_role(user_id, tenant_id):
        return "project_admin"

    async def _owned(run_id, tenant_id):
        return ws.RunSelection(model_id="m1", offering_id="o1", project_id=project_id)

    monkeypatch.setattr(ws, "_redeem_ws_ticket", _fake_redeem)
    monkeypatch.setattr(ws, "_resolve_platform_role", _fake_role)
    monkeypatch.setattr(ws, "_resolve_run", _owned)
    # This caller administers the run's project. The per-project check
    # (`_project_admin_tier_for_run`) is exercised on its own in
    # test_ws_project_scope.py; stubbing it here keeps these tests about what they are
    # each named for, rather than making every one of them a role-binding fixture.
    async def _permissions(user_id, tenant_id):
        return ["agent:use"]

    async def _tier(project_id, tenant_id, *, user_id, permissions):
        return "project"

    monkeypatch.setattr(ws, "_resolve_permissions", _permissions)
    monkeypatch.setattr(ws, "_project_admin_tier_for_run", _tier)


def _record_run_agent(monkeypatch, ws, events=None):
    calls = []

    async def _fake_run_agent(agent_id, **kwargs):
        calls.append((agent_id, kwargs))
        for event in (events if events is not None else [{"type": "stream_end"}]):
            yield event

    monkeypatch.setattr(ws, "run_agent", _fake_run_agent)
    return calls


def _record_route(monkeypatch, ws, decision):
    calls = []

    async def _fake_route(text, **kwargs):
        calls.append((text, kwargs))
        return decision

    monkeypatch.setattr(ws, "route", _fake_route)
    return calls


def _no_context(monkeypatch, ws):
    async def _fake(run_id, tenant_id, target_agent):
        return ""

    monkeypatch.setattr(ws, "handoff_context", _fake)


def _frame(**kwargs):
    base = {"type": "user_message", "text": "hi", "run_id": _RUN}
    base.update(kwargs)
    return json.dumps(base)


async def _serve(ws, frames, **params):
    socket = _FakeWebSocket(params={"ticket": "tkt", **params}, inbound=frames)
    await ws.orchestrator2_ws(socket)
    return socket


# ── a message with no agent is ROUTED, not refused ───────────────────────────


@pytest.mark.asyncio
async def test_a_message_with_no_agent_is_routed_not_refused(monkeypatch):
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws)
    _no_context(monkeypatch, ws)
    routed = _record_route(monkeypatch, ws, rtr.RoutingDecision(
        agent_id="requirements", reason="You asked for a PRD.", direct_reply=None))
    dispatched = _record_run_agent(monkeypatch, ws)

    socket = await _serve(ws, [_frame(text="I need a PRD")])

    assert [t for t, _ in routed] == ["I need a PRD"]
    assert [a for a, _ in dispatched] == ["requirements"]
    assert not [e for e in socket.sent if e["type"] == "error"]


@pytest.mark.asyncio
async def test_the_router_is_given_the_runs_project_never_the_frames(monkeypatch):
    """The BYOK gap Phase 2 closed, on a new code path. The router makes its OWN model
    call; without the run's project it resolves against no grant and no budget."""
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws, project_id="proj-FROM-RUN")
    _no_context(monkeypatch, ws)
    routed = _record_route(monkeypatch, ws, rtr.RoutingDecision(
        agent_id="design", reason="r", direct_reply=None))
    _record_run_agent(monkeypatch, ws)

    await _serve(ws, [_frame(project_id="proj-FROM-CLIENT")])

    _, kwargs = routed[0]
    assert kwargs["project_id"] == "proj-FROM-RUN"
    assert kwargs["tenant_id"] == "t1"
    assert kwargs["run_id"] == _RUN
    assert kwargs["model_id"] == "m1" and kwargs["offering_id"] == "o1"


@pytest.mark.asyncio
async def test_an_unowned_run_is_refused_before_the_router_spends_a_model_call(monkeypatch):
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws)
    _no_context(monkeypatch, ws)
    routed = _record_route(monkeypatch, ws, rtr.RoutingDecision(
        agent_id="design", reason="r", direct_reply=None))
    dispatched = _record_run_agent(monkeypatch, ws)

    async def _refuse(run_id, tenant_id):
        raise ws.RunNotAvailableError("no such run")

    monkeypatch.setattr(ws, "_resolve_run", _refuse)
    socket = await _serve(ws, [_frame()])

    assert routed == [], "ownership is checked BEFORE routing, not after"
    assert dispatched == []
    assert any(e["type"] == "error" for e in socket.sent)
    assert socket.sent[-1]["type"] == "stream_end"


# ── an explicit agent OVERRIDES the router ───────────────────────────────────


@pytest.mark.asyncio
async def test_an_explicit_agent_overrides_the_router(monkeypatch):
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws)
    _no_context(monkeypatch, ws)
    routed = _record_route(monkeypatch, ws, rtr.RoutingDecision(
        agent_id="requirements", reason="the router would have said this",
        direct_reply=None))
    dispatched = _record_run_agent(monkeypatch, ws)

    await _serve(ws, [_frame(agent="security")])

    assert routed == [], "a named agent must not cost a routing call"
    assert [a for a, _ in dispatched] == ["security"]


@pytest.mark.asyncio
async def test_an_explicit_agent_says_so_in_its_reason(monkeypatch):
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws)
    _no_context(monkeypatch, ws)
    dispatched = _record_run_agent(monkeypatch, ws)

    await _serve(ws, [_frame(agent="security")])

    _, kwargs = dispatched[0]
    assert kwargs["reason"], "the user must be told why this agent is answering"
    assert "named" in kwargs["reason"].lower() or "asked" in kwargs["reason"].lower()


# ── the decision is announced, once, with its reason ─────────────────────────


@pytest.mark.asyncio
async def test_the_routers_reason_reaches_the_wire_exactly_once(monkeypatch):
    """`run_agent` emits its own `agent.selected`. Two of them for one turn would have
    the second erasing the first's explanation, which is the whole point of the event.
    """
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws)
    _no_context(monkeypatch, ws)
    _record_route(monkeypatch, ws, rtr.RoutingDecision(
        agent_id="requirements", reason="Because you asked for a PRD.",
        direct_reply=None))

    async def _echoing_run_agent(agent_id, **kwargs):
        yield {"type": "agent.selected", "agent": agent_id,
               "reason": kwargs["reason"], "run_id": kwargs["run_id"]}
        yield {"type": "stream_end"}

    monkeypatch.setattr(ws, "run_agent", _echoing_run_agent)
    socket = await _serve(ws, [_frame()])

    selected = [e for e in socket.sent if e["type"] == "agent.selected"]
    assert len(selected) == 1, f"exactly one agent.selected per turn, got {selected}"
    assert selected[0]["reason"] == "Because you asked for a PRD."


# ── answering directly loads no graph ────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_direct_reply_runs_no_agent_at_all(monkeypatch):
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws)
    _no_context(monkeypatch, ws)
    _record_route(monkeypatch, ws, rtr.RoutingDecision(
        agent_id=None, reason="No delivery agent was needed.",
        direct_reply="Nine agents are available on this project."))
    dispatched = _record_run_agent(monkeypatch, ws)

    socket = await _serve(ws, [_frame(text="what can you do?")])

    assert dispatched == [], "answering directly must not load a graph"
    assert [e["type"] for e in socket.sent] == ["stream_chunk", "stream_end"]
    assert socket.sent[0]["content"] == "Nine agents are available on this project."


@pytest.mark.asyncio
async def test_a_direct_reply_emits_no_agent_selected(monkeypatch):
    """`AgentSelectedEvent.agent` is the strict nine-value enum in protocol.ts. An
    `agent.selected` with a null agent fails safeParse and vanishes in the browser;
    widening the enum is the change that already cost this branch one dropped event
    type. Absence of the badge IS the signal that no agent ran."""
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws)
    _no_context(monkeypatch, ws)
    _record_route(monkeypatch, ws, rtr.RoutingDecision(
        agent_id=None, reason="none needed", direct_reply="Here is your answer."))
    _record_run_agent(monkeypatch, ws)

    socket = await _serve(ws, [_frame()])
    assert not [e for e in socket.sent if e["type"] == "agent.selected"]


@pytest.mark.asyncio
async def test_a_routing_failure_becomes_a_typed_error_not_a_dead_socket(monkeypatch):
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws)
    _no_context(monkeypatch, ws)

    async def _boom(text, **kwargs):
        raise RuntimeError("the routing model refused")

    monkeypatch.setattr(ws, "route", _boom)
    _record_run_agent(monkeypatch, ws)

    socket = await _serve(ws, [_frame(), _frame()])

    errors = [e for e in socket.sent if e["type"] == "error"]
    assert len(errors) == 2, "the socket must survive a routing failure and serve on"
    assert socket.sent[-1]["type"] == "stream_end"


# ── the conversation the router reads ────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_router_sees_the_earlier_turns_of_this_connection(monkeypatch):
    """'and now?' is only routable against what came before it."""
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws)
    _no_context(monkeypatch, ws)
    routed = _record_route(monkeypatch, ws, rtr.RoutingDecision(
        agent_id="design", reason="r", direct_reply=None))

    async def _replying(agent_id, **kwargs):
        yield {"type": "stream_chunk", "content": "the design is ready"}
        yield {"type": "stream_end"}

    monkeypatch.setattr(ws, "run_agent", _replying)
    await _serve(ws, [_frame(text="design the billing service"), _frame(text="and now?")])

    assert len(routed) == 2
    history = routed[1][1]["history"]
    rendered = json.dumps(history)
    assert "design the billing service" in rendered, "the user's earlier turn is missing"
    assert "the design is ready" in rendered, (
        "the agent's reply is missing — routing on the user's half of the conversation "
        "alone is what made 'and now?' unroutable"
    )


@pytest.mark.asyncio
async def test_history_is_not_taken_from_the_client(monkeypatch):
    """A client-supplied history would be caller-controlled text steering a routing
    decision. The socket keeps its own."""
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws)
    _no_context(monkeypatch, ws)
    routed = _record_route(monkeypatch, ws, rtr.RoutingDecision(
        agent_id="design", reason="r", direct_reply=None))
    _record_run_agent(monkeypatch, ws)

    await _serve(ws, [json.dumps({
        "type": "user_message", "text": "hi", "run_id": _RUN,
        "history": [{"role": "user", "content": "PLANTED-BY-CLIENT"}],
    })])

    assert "PLANTED-BY-CLIENT" not in json.dumps(routed[0][1]["history"])


@pytest.mark.asyncio
async def test_history_does_not_grow_without_bound(monkeypatch):
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws)
    _no_context(monkeypatch, ws)
    routed = _record_route(monkeypatch, ws, rtr.RoutingDecision(
        agent_id="design", reason="r", direct_reply=None))
    _record_run_agent(monkeypatch, ws, [{"type": "stream_chunk", "content": "ok"},
                                        {"type": "stream_end"}])

    await _serve(ws, [_frame(text=f"turn {i}") for i in range(40)])

    assert len(routed) == 40
    assert len(routed[-1][1]["history"]) <= rtr._HISTORY_LIMIT


@pytest.mark.asyncio
async def test_each_connection_starts_with_an_empty_history(monkeypatch):
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws)
    _no_context(monkeypatch, ws)
    routed = _record_route(monkeypatch, ws, rtr.RoutingDecision(
        agent_id="design", reason="r", direct_reply=None))
    _record_run_agent(monkeypatch, ws)

    await _serve(ws, [_frame(text="first connection")])
    await _serve(ws, [_frame(text="second connection")])

    assert routed[1][1]["history"] == []


# ── earlier agents' work reaches the chosen agent ────────────────────────────


@pytest.mark.asyncio
async def test_the_chosen_agent_is_given_what_the_run_already_holds(monkeypatch):
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws)
    _record_route(monkeypatch, ws, rtr.RoutingDecision(
        agent_id="design", reason="r", direct_reply=None))
    dispatched = _record_run_agent(monkeypatch, ws)

    seen = []

    async def _context(run_id, tenant_id, target_agent):
        seen.append((run_id, tenant_id, target_agent))
        return "### Requirements artifact\nR1"

    monkeypatch.setattr(ws, "handoff_context", _context)
    await _serve(ws, [_frame()])

    assert seen == [(_RUN, "t1", "design")]
    assert dispatched[0][1]["context"] == "### Requirements artifact\nR1"


@pytest.mark.asyncio
async def test_a_context_failure_is_reported_not_silently_dropped(monkeypatch):
    """`handoff_context` raises when it could not READ the run. Swallowing that would
    tell the agent "there is no prior work" — the exact defect the old engine's
    `_upstream_context` had, reintroduced one layer up."""
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws)
    _record_route(monkeypatch, ws, rtr.RoutingDecision(
        agent_id="design", reason="r", direct_reply=None))
    dispatched = _record_run_agent(monkeypatch, ws)

    async def _unavailable(run_id, tenant_id, target_agent):
        raise RuntimeError("the run's artifacts could not be read")

    monkeypatch.setattr(ws, "handoff_context", _unavailable)
    socket = await _serve(ws, [_frame()])

    assert dispatched == [], "an agent must not run believing the run is empty"
    assert any(e["type"] == "error" for e in socket.sent)
    assert socket.sent[-1]["type"] == "stream_end"
