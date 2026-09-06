"""Being a Project Admin somewhere is not being a Project Admin HERE.

The socket's standing check asks "is this caller a Project Admin anywhere in this
tenant" — the same wrong question `shared/authz/project_scope.py`'s module docstring was
written about, where five routers took `{project_id}` from the path and filtered on
tenant alone. Phase 2 deferred the per-project half; routing makes it load-bearing,
because the Orchestrator reaches all nine agents at once and every turn spends the RUN's
project's model grant and the RUN's project's budget.

Concretely, without this check: a Project Admin of project A names a run belonging to
project B in the same tenant, and drives all nine agents against it on B's BYOK key and
B's budget. Tenant scoping cannot see it — both runs are in one tenant — so a test using
two tenants proves nothing here.

The rule is NOT re-implemented on the socket. `project_admin_tier_for` in
`shared/authz/project_scope.py` is the same function the HTTP routers reach through
`project_admin_tier`, addressed by identity because a WebSocket has no `Request`; that
the two are the same rule is pinned by
`tests/test_project_admin_tier_addressed_by_identity.py`.
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


def _patch(monkeypatch, ws, *, tier, project_id="proj-B", permissions=("agent:use",)):
    """A caller who passes the STANDING check, on a run belonging to `project_id`.

    `tier` is what the shared rule says about this caller and THAT project: "org",
    "unit", "project", or None for "does not run it".
    """
    async def _redeem(ticket):
        return {"user_id": "u1", "tenant_id": "t1"}

    async def _role(user_id, tenant_id):
        return "project_admin"

    async def _perms(user_id, tenant_id):
        return list(permissions)

    async def _run(run_id, tenant_id):
        return ws.RunSelection(model_id="m", offering_id="o", project_id=project_id)

    seen = []

    async def _tier(run_project_id, tenant_id, *, user_id, permissions):
        seen.append((run_project_id, tenant_id, user_id, tuple(permissions)))
        return tier

    monkeypatch.setattr(ws, "_redeem_ws_ticket", _redeem)
    monkeypatch.setattr(ws, "_resolve_platform_role", _role)
    monkeypatch.setattr(ws, "_resolve_permissions", _perms)
    monkeypatch.setattr(ws, "_resolve_run", _run)
    monkeypatch.setattr(ws, "_project_admin_tier_for_run", _tier)
    return seen


def _stub_turn(monkeypatch, ws):
    routed, dispatched = [], []

    async def _route(text, **kwargs):
        routed.append(text)
        return rtr.RoutingDecision(agent_id="design", reason="r", direct_reply=None)

    async def _context(run_id, tenant_id, target_agent):
        return ""

    async def _run_agent(agent_id, **kwargs):
        dispatched.append(agent_id)
        yield {"type": "stream_end"}

    monkeypatch.setattr(ws, "route", _route)
    monkeypatch.setattr(ws, "handoff_context", _context)
    monkeypatch.setattr(ws, "run_agent", _run_agent)
    return routed, dispatched


async def _serve(ws, frames):
    socket = _FakeWebSocket(params={"ticket": "tkt"}, inbound=frames)
    await ws.orchestrator2_ws(socket)
    return socket


def _frame(**kw):
    base = {"type": "user_message", "text": "hi", "run_id": _RUN}
    base.update(kw)
    return json.dumps(base)


# ── the test this task exists for ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_project_admin_of_another_project_cannot_drive_this_run(monkeypatch):
    """Project A's admin, project B's run, ONE tenant. The standing check passes and
    must not be the last word."""
    from agents_orchestrator.orchestrator2 import ws
    _patch(monkeypatch, ws, tier=None, project_id="proj-B")
    routed, dispatched = _stub_turn(monkeypatch, ws)

    socket = await _serve(ws, [_frame()])

    assert dispatched == [], "an agent ran against a project this caller does not run"
    assert routed == [], "refused before the router could spend a model call"
    assert any(e["type"] == "error" for e in socket.sent)
    assert socket.sent[-1]["type"] == "stream_end", "the turn must still terminate"


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["project", "unit", "org"])
async def test_everyone_who_actually_runs_the_project_is_admitted(monkeypatch, tier):
    """A check that refuses everybody passes the test above perfectly. All three tiers
    legitimately run a project: its own admin, its parent unit's admin, and org-wide."""
    from agents_orchestrator.orchestrator2 import ws
    _patch(monkeypatch, ws, tier=tier)
    routed, dispatched = _stub_turn(monkeypatch, ws)

    socket = await _serve(ws, [_frame()])

    assert dispatched == ["design"]
    assert not [e for e in socket.sent if e["type"] == "error"]


@pytest.mark.asyncio
async def test_the_check_is_asked_about_the_runs_project_and_the_ticket_identity(
    monkeypatch,
):
    """The project comes from the verified `runs` row and the identity from the
    redeemed ticket. A frame-supplied project would let the caller name one they DO
    administer while the turn spends another's grant."""
    from agents_orchestrator.orchestrator2 import ws
    seen = _patch(monkeypatch, ws, tier="project", project_id="proj-FROM-RUN",
                  permissions=("agent:use", "artifact:view"))
    _stub_turn(monkeypatch, ws)

    await _serve(ws, [_frame(project_id="proj-FROM-CLIENT")])

    assert seen == [("proj-FROM-RUN", "t1", "u1", ("agent:use", "artifact:view"))]


@pytest.mark.asyncio
async def test_a_run_with_no_project_is_refused(monkeypatch):
    """`Run.project_id` is nullable since migration 0005 — a webhook run carries a
    provider key, not a local project UUID. There is no project to administer, so there
    is nothing to check against, and "no project" must not become "no check". It also
    could not be scoped for models or budget even if it were let through."""
    from agents_orchestrator.orchestrator2 import ws
    _patch(monkeypatch, ws, tier="project", project_id=None)
    routed, dispatched = _stub_turn(monkeypatch, ws)

    socket = await _serve(ws, [_frame()])

    assert dispatched == [] and routed == []
    assert any(e["type"] == "error" for e in socket.sent)


@pytest.mark.asyncio
async def test_the_refusal_does_not_reveal_whether_the_run_exists(monkeypatch):
    """`RunNotAvailableError` words "absent" and "another tenant's" identically so the
    message cannot become an existence oracle. A distinct "that project is not yours"
    would reintroduce exactly that, one level along: it confirms the run exists."""
    from agents_orchestrator.orchestrator2 import ws

    _patch(monkeypatch, ws, tier=None)
    _stub_turn(monkeypatch, ws)
    refused = await _serve(ws, [_frame()])

    async def _absent(run_id, tenant_id):
        raise ws.RunNotAvailableError("no such run")

    _patch(monkeypatch, ws, tier="project")
    _stub_turn(monkeypatch, ws)
    monkeypatch.setattr(ws, "_resolve_run", _absent)
    missing = await _serve(ws, [_frame()])

    def _messages(socket):
        return [e.get("message") for e in socket.sent if e["type"] == "error"]

    assert _messages(refused) == _messages(missing), (
        f"the two refusals are distinguishable: {_messages(refused)} vs "
        f"{_messages(missing)}"
    )


@pytest.mark.asyncio
async def test_a_failure_resolving_the_tier_refuses_rather_than_admits(monkeypatch):
    """Fails closed, like `_resolve_platform_role` and `_resolve_run`. A database blip
    must deny the turn, never open a project up."""
    from agents_orchestrator.orchestrator2 import ws
    _patch(monkeypatch, ws, tier="project")
    routed, dispatched = _stub_turn(monkeypatch, ws)

    async def _boom(run_project_id, tenant_id, *, user_id, permissions):
        raise RuntimeError("the database is unreachable")

    monkeypatch.setattr(ws, "_project_admin_tier_for_run", _boom)
    socket = await _serve(ws, [_frame()])

    assert dispatched == [] and routed == []
    assert any(e["type"] == "error" for e in socket.sent)


@pytest.mark.asyncio
async def test_unresolvable_permissions_refuse_the_connection(monkeypatch):
    """Permissions are resolved once per connection and feed the per-project rule. If
    they cannot be resolved the socket must close rather than continue with `[]`, which
    `read_scope` warns is a REAL answer meaning 'no standing' and must never stand in
    for 'could not tell'."""
    from agents_orchestrator.orchestrator2 import ws
    _patch(monkeypatch, ws, tier="project")

    async def _unresolvable(user_id, tenant_id):
        return None

    monkeypatch.setattr(ws, "_resolve_permissions", _unresolvable)
    socket = await _serve(ws, [_frame()])

    assert not socket.accepted, "the socket must be refused before the handshake"
    assert socket.closed is not None and socket.closed[0] == 4403
