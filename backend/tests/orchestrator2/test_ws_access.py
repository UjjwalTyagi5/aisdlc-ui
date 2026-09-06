"""The Orchestrator socket's access check and turn contract.

The first two tests are the brief's source-level guards. Everything after them
drives the real handler with a fake WebSocket, because a `grep` for the string
"project_admin" proves only that the string is present — the Phase 1 review found
a per-project Orchestrator route that was gated in the nav, gated in the global
route, gated on the button, and completely open to anyone who typed the URL. The
behavioural tests below are what actually prove the socket refuses.
"""
import ast
import inspect
import json
import re
import textwrap
from pathlib import Path

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

    async def _owned_run(run_id, tenant_id):
        return ws.RunSelection(model_id=None, offering_id=None, project_id=None)

    monkeypatch.setattr(ws, "_resolve_run", _owned_run)


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
async def test_a_message_with_no_agent_is_chosen_for_openly_never_silently(monkeypatch):
    """Phase 2 REFUSED a frame naming no agent; Phase 3 routes it. What must not
    change is the reason that refusal existed: the old engine picked an agent and told
    nobody, so a wrong pick was invisible until the answer made no sense. A routed
    turn is still a choice the user can see and correct — `agent.selected` names the
    agent AND says why, before any of its text.

    So this test is no longer about refusing. It is about the choice being ANNOUNCED,
    which is the invariant the refusal was standing in for while there was no router.
    """
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws, role="project_admin")

    async def _routed(text, **kwargs):
        from agents_orchestrator.orchestrator2.router import RoutingDecision
        return RoutingDecision(agent_id="requirements",
                               reason="You asked for a PRD.", direct_reply=None)

    async def _no_context(run_id, tenant_id, target_agent):
        return ""

    monkeypatch.setattr(ws, "route", _routed)
    monkeypatch.setattr(ws, "handoff_context", _no_context)

    async def _run_agent(agent_id, **kwargs):
        yield {"type": "agent.selected", "agent": agent_id,
               "reason": kwargs["reason"], "run_id": kwargs["run_id"]}
        yield {"type": "stream_end"}

    monkeypatch.setattr(ws, "run_agent", _run_agent)

    socket = _FakeWebSocket(
        params={"ticket": "tkt"},
        inbound=[json.dumps({"type": "user_message", "text": "I need a PRD",
                             "run_id": "r1", "project_id": "p1"})],
    )
    await ws.orchestrator2_ws(socket)

    assert not [e for e in socket.events if e["type"] == "error"]
    selected = [e for e in socket.events if e["type"] == "agent.selected"]
    assert len(selected) == 1
    assert selected[0]["agent"] == "requirements"
    assert selected[0]["reason"] == "You asked for a PRD.", (
        "a chosen agent must arrive with the reason it was chosen — an unexplained "
        "choice is the silent dispatch this engine was rebuilt to end"
    )
    assert socket.events[0]["type"] == "agent.selected", "announced BEFORE any text"
    assert socket.events[-1]["type"] == "stream_end"


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


def _code_of(fn) -> str:
    """A function's source with its docstring and comments stripped.

    Assertions about what a function DOES must not be satisfiable — or defeated — by
    what its prose SAYS about it.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    node = tree.body[0]
    if (node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)):
        node.body = node.body[1:]
    return ast.unparse(node)


def _frontend_event_types() -> set[str]:
    """The event `type` literals the frontend's Zod union accepts, READ OUT OF
    `frontend/lib/orchestrator/protocol.ts`.

    Hardcoding this set here would pin a copy, not the contract: the frontend could
    rename an event and this test would keep passing while the browser silently
    dropped every frame of that type. Parsing the real file is the only version of
    this test that can catch drift.
    """
    protocol = (Path(__file__).resolve().parents[3]
                / "frontend" / "lib" / "orchestrator" / "protocol.ts")
    assert protocol.is_file(), f"the frontend contract is missing: {protocol}"
    literals = set(re.findall(r'z\.literal\("([^"]+)"\)', protocol.read_text(encoding="utf-8")))
    # Guard the guard: a regex that stopped matching would make this vacuous.
    assert len(literals) >= 6, f"parsed too few event literals from {protocol}: {literals}"
    return literals


@pytest.mark.asyncio
async def test_every_emitted_event_type_is_in_the_frontend_union(monkeypatch):
    """A type string differing even slightly means the browser's Zod union silently
    drops the frame. The allowed set is parsed from the frontend contract itself, so
    a rename there fails here."""
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws, role="project_admin")
    _record_run_agent(monkeypatch, ws, [{"type": "stream_end"}])

    valid = _frontend_event_types()
    # The types this socket emits on its own are the two the union must contain for
    # any of the tests below to mean anything.
    assert {"error", "stream_end"} <= valid, valid

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


# ── the role resolver itself ─────────────────────────────────────────────────
#
# Every test above stubs `_resolve_platform_role`, which proves the COMPARISON and
# nothing about the RESOLUTION. The tests here patch one layer lower — the two
# resolver functions in `shared.authz` — so the body of `_resolve_platform_role` runs
# for real. Without them, passing `[]` instead of the caller's permissions, or an
# `except` that returned a role instead of None, would pass the whole suite.


def _patch_resolvers(monkeypatch, *, permissions=None, role=None,
                     perms_raises=None, role_raises=None):
    import shared.authz.effective_role as role_mod
    import shared.authz.resolver as resolver_mod
    seen = {}

    async def _perms(user_id, tenant_id):
        if perms_raises:
            raise perms_raises
        return list(permissions or [])

    async def _role(user_id, tenant_id, perms):
        if role_raises:
            raise role_raises
        seen["permissions"] = perms
        seen["identity"] = (user_id, tenant_id)
        return role

    monkeypatch.setattr(resolver_mod, "resolve_permissions_for_user", _perms)
    monkeypatch.setattr(role_mod, "resolve_platform_role_for_user", _role)
    return seen


@pytest.mark.asyncio
async def test_the_real_resolver_forwards_the_callers_permissions(monkeypatch):
    """`platform_role_for` reads org-wide standing out of the PERMISSION list — a
    caller can be an Organization Admin through `settings:manage` with no org_admin
    binding row. Passing [] here would hide such a caller behind whatever lesser
    binding they also hold and admit them as a Project Admin."""
    from agents_orchestrator.orchestrator2 import ws
    seen = _patch_resolvers(monkeypatch, permissions=["settings:manage"], role="org_admin")

    assert await ws._resolve_platform_role("u-42", "t-9") == "org_admin"
    assert seen["permissions"] == ["settings:manage"], (
        "the resolver must receive the caller's real permissions, not []"
    )
    assert seen["identity"] == ("u-42", "t-9")


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["permissions", "role"])
async def test_the_real_resolver_fails_closed_when_a_resolver_raises(monkeypatch, which):
    """A DB outage must deny the Orchestrator, never open it."""
    from agents_orchestrator.orchestrator2 import ws
    boom = RuntimeError("database is on fire")
    _patch_resolvers(
        monkeypatch,
        role="project_admin",
        perms_raises=boom if which == "permissions" else None,
        role_raises=boom if which == "role" else None,
    )

    assert await ws._resolve_platform_role("u1", "t1") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id,tenant_id", [("", "t1"), ("u1", ""), ("", "")])
async def test_the_real_resolver_refuses_without_a_full_identity(monkeypatch, user_id, tenant_id):
    from agents_orchestrator.orchestrator2 import ws
    _patch_resolvers(monkeypatch, role="project_admin")

    assert await ws._resolve_platform_role(user_id, tenant_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("resolved,accepted", [("project_admin", True), ("developer", False)])
async def test_the_socket_admits_and_refuses_through_the_real_resolver(monkeypatch, resolved, accepted):
    """End to end with `_resolve_platform_role` NOT stubbed: only the two shared.authz
    resolvers are replaced, so the socket's own resolution path runs."""
    from agents_orchestrator.orchestrator2 import ws

    async def _fake_redeem(ticket):
        return {"user_id": "u1", "tenant_id": "t1"}

    monkeypatch.setattr(ws, "_redeem_ws_ticket", _fake_redeem)
    _patch_resolvers(monkeypatch, permissions=[], role=resolved)
    calls = _record_run_agent(monkeypatch, ws, [{"type": "stream_end"}])

    socket = _FakeWebSocket(params={"ticket": "tkt"}, inbound=[])
    await ws.orchestrator2_ws(socket)

    assert socket.accepted is accepted
    if not accepted:
        assert socket.closed is not None and socket.closed[0] == 4403
        assert calls == []


@pytest.mark.asyncio
async def test_the_socket_refuses_when_the_real_resolver_hits_a_dead_database(monkeypatch):
    from agents_orchestrator.orchestrator2 import ws

    async def _fake_redeem(ticket):
        return {"user_id": "u1", "tenant_id": "t1"}

    monkeypatch.setattr(ws, "_redeem_ws_ticket", _fake_redeem)
    _patch_resolvers(monkeypatch, perms_raises=RuntimeError("no database"))

    socket = _FakeWebSocket(params={"ticket": "tkt"}, inbound=[])
    await ws.orchestrator2_ws(socket)

    assert socket.accepted is False
    assert socket.closed is not None and socket.closed[0] == 4403


# ── run ownership ────────────────────────────────────────────────────────────
#
# `run_id` arrives on the wire and becomes the LangGraph thread_id on a PERSISTENT
# checkpointer. Being a Project Admin says you may drive the Orchestrator; it does
# not say which runs are yours. The first version of this socket read the run through
# an RLS-BYPASSING superuser session with no tenant predicate, so a Project Admin in
# tenant A could name a run in tenant B, read its model selection, and join its thread.

_A_RUN = "11111111-1111-1111-1111-111111111111"
_A_TENANT = "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
async def test_a_run_the_caller_does_not_own_never_reaches_the_agent(monkeypatch):
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws, role="project_admin")
    calls = _record_run_agent(monkeypatch, ws, [{"type": "stream_end"}])

    async def _not_yours(run_id, tenant_id):
        raise ws.RunNotAvailableError("no such run")

    monkeypatch.setattr(ws, "_resolve_run", _not_yours)

    socket = _FakeWebSocket(
        params={"ticket": "tkt"},
        inbound=[json.dumps({"type": "user_message", "text": "hi", "agent": "design",
                             "run_id": _A_RUN, "project_id": "p1"})],
    )
    await ws.orchestrator2_ws(socket)

    assert calls == [], "an unowned run must never become a graph thread_id"
    assert [e["type"] for e in socket.events] == ["error", "stream_end"]


@pytest.mark.asyncio
async def test_the_run_query_is_scoped_to_the_callers_tenant(monkeypatch):
    """The regression itself: the query must carry the caller's tenant, and the
    session it runs in must be the tenant-scoped one, not the superuser session that
    bypasses row-level security."""
    from agents_orchestrator.orchestrator2 import ws
    # Code only: the docstring NAMES the superuser session while explaining the bug it
    # fixed, so a raw source scan would trip over its own explanation.
    src = _code_of(ws._resolve_run)
    assert "Run.tenant_id" in src, "the run lookup must filter on the caller's tenant"
    assert "get_db_session_for_tenant" in src
    assert "get_db_session_superuser" not in src, (
        "a superuser session bypasses RLS — the tenant filter would be the only "
        "thing between tenants"
    )
    # And the caller's tenant, from the redeemed ticket, is what gets passed in.
    handler = inspect.getsource(ws.orchestrator2_ws)
    assert "_resolve_run(run_id, tenant_id)" in handler


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "   ", "not-a-uuid", "r-live", "1234"])
async def test_a_run_id_that_is_not_a_uuid_is_refused_without_touching_the_database(monkeypatch, bad):
    """A client-invented conversation key must not become a graph thread_id, and
    unvalidated text must not reach a UUID comparison."""
    from agents_orchestrator.orchestrator2 import ws
    touched = []

    def _record_db(tenant_id):
        # NOT `raise AssertionError` — `_resolve_run` catches everything around the
        # query and converts it to RunNotAvailableError, so an assertion raised in
        # here would be laundered into the very exception the test expects and the
        # test would pass while the database WAS consulted.
        touched.append(tenant_id)
        raise RuntimeError("db")

    monkeypatch.setattr("shared.db.get_db_session_for_tenant", _record_db)

    with pytest.raises(ws.RunNotAvailableError):
        await ws._resolve_run(bad, _A_TENANT)
    assert touched == [], "a non-UUID run id must be refused before any query"


@pytest.mark.asyncio
async def test_an_unverifiable_run_refuses_instead_of_defaulting(monkeypatch):
    """The code this replaced fell through to (None, None) — the organization default
    — on any DB error. A turn that cannot prove which run it belongs to must not run."""
    from agents_orchestrator.orchestrator2 import ws

    def _dead_db(tenant_id):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("shared.db.get_db_session_for_tenant", _dead_db)

    with pytest.raises(ws.RunNotAvailableError):
        await ws._resolve_run(_A_RUN, _A_TENANT)


@pytest.mark.asyncio
async def test_an_owned_runs_model_selection_is_passed_to_the_agent(monkeypatch):
    """The model is a property of the run, never something the client names."""
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws, role="project_admin")
    calls = _record_run_agent(monkeypatch, ws, [{"type": "stream_end"}])

    async def _owned(run_id, tenant_id):
        assert (run_id, tenant_id) == (_A_RUN, "t1")
        return ws.RunSelection(model_id="claude-x", offering_id="offering-7",
                               project_id="proj-from-the-run")

    monkeypatch.setattr(ws, "_resolve_run", _owned)

    socket = _FakeWebSocket(
        params={"ticket": "tkt"},
        inbound=[json.dumps({"type": "user_message", "text": "hi", "agent": "design",
                             "run_id": _A_RUN, "model_id": "attacker-chosen",
                             "offering_id": "attacker-chosen"})],
    )
    await ws.orchestrator2_ws(socket)

    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs["model_id"] == "claude-x"
    assert kwargs["offering_id"] == "offering-7"


# ── serialization ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unencodable_event_is_reported_not_laundered_into_a_hang_up(monkeypatch):
    """`_send` used to catch everything and re-raise WebSocketDisconnect, so an event
    that could not be JSON-encoded looked exactly like the client closing the tab:
    the loop unwound, the socket dropped, and nothing was ever sent."""
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws, role="project_admin")
    _record_run_agent(monkeypatch, ws, [
        {"type": "stream_chunk", "content": object()},   # not JSON-encodable
        {"type": "stream_end"},
    ])

    socket = _FakeWebSocket(
        params={"ticket": "tkt"},
        inbound=[json.dumps({"type": "user_message", "text": "hi", "agent": "design",
                             "run_id": _A_RUN})],
    )
    await ws.orchestrator2_ws(socket)

    types = [e["type"] for e in socket.events]
    assert "error" in types, "an unencodable event must be reported, not swallowed"
    assert types[-1] == "stream_end"


@pytest.mark.asyncio
async def test_a_dead_peer_still_ends_the_loop_quietly(monkeypatch):
    """The other half of the same split: a genuinely dead socket has nobody to report
    to, so it must NOT try to send an error — it just ends."""
    from agents_orchestrator.orchestrator2 import ws
    _patch_auth(monkeypatch, ws, role="project_admin")
    _record_run_agent(monkeypatch, ws, [{"type": "stream_chunk", "content": "hi"}])

    class _DeadPeer(_FakeWebSocket):
        async def send_text(self, raw):
            raise RuntimeError("peer is gone")

    socket = _DeadPeer(
        params={"ticket": "tkt"},
        inbound=[json.dumps({"type": "user_message", "text": "hi", "agent": "design",
                             "run_id": _A_RUN})],
    )
    await ws.orchestrator2_ws(socket)  # must return, not raise

    assert socket.sent == []
