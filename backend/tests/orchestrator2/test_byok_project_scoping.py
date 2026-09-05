"""Project-scoped BYOK in the Orchestrator engine.

THE BUG THESE TESTS PIN. `orchestrator2` threaded `model_id` / `offering_id` into
graph state and called `resolve_model_for_run` nowhere at all. Nothing set the
`_RESOLVED_MODEL` contextvar that every agent's `build_llm` reads, so a turn ran on
whatever the agent could find — in practice the platform's `ANTHROPIC_API_KEY`,
aliased `local-env:anthropic`. That is the same silent env fallback that had the
Testing agent billing the platform's own account while an administrator looked at a
correctly-configured BYOK provider.

`project_id` is the load-bearing half. `resolve_model_for_run` uses it for TWO
enforcement decisions:

  · `effective_project_offerings(tenant_id, project_id)` — WHICH models this project
    was granted. With `None`, a tenant that has any grant rows matches none.
  · `check_budgets(tenant_id, project_id)` — WHOSE monthly cap this spend counts
    against. With `None`, the project's cap is simply not consulted.

So "no project id" is not a smaller version of the check; it is the check not
happening. Every test below fails against the pre-fix code.
"""
import ast
import inspect
import json
import uuid

import pytest
from fastapi import WebSocketDisconnect

from agents_orchestrator.orchestrator2 import dispatch, registry as reg
from shared.services import model_resolver as mr


# ── harness ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_contextvars():
    """Both contextvars are process-wide within a task context; a value left behind
    by one test would let the next one pass for the wrong reason."""
    mr.set_resolved_model(None)
    mr.set_run_project(None)
    yield
    mr.set_resolved_model(None)
    mr.set_run_project(None)


def _a_resolved_model(model="claude-sonnet-4-6", offering="offering-A"):
    return mr.ResolvedModel(
        provider="anthropic", litellm_provider="anthropic", model=model,
        api_key="sk-tenant-key", base_url=None, alias="tenant:t1:prov-1",
        offering_id=offering, display_name="Project A's key",
    )


class _Recorder:
    """An ordered log of everything that happened during a turn, so ordering can be
    asserted rather than mere co-occurrence."""

    def __init__(self):
        self.log = []                 # ordered step names
        self.resolve_calls = []       # kwargs each resolve_model_for_run saw
        self.graph_loads = 0
        self.model_at_graph = "<never invoked>"
        self.project_at_graph = "<never invoked>"


def _install(monkeypatch, recorder, *, agent_id="design", mode="stream",
             resolved=None, raises=None):
    """Wire a fake resolver and a fake graph for `agent_id`, both reporting into
    `recorder`. Returns nothing — everything observable lands on the recorder."""

    async def _fake_resolve(tenant_id, requested_model_id=None, **kwargs):
        recorder.log.append("resolve")
        recorder.resolve_calls.append(
            {"tenant_id": tenant_id, "requested_model_id": requested_model_id,
             # Read the run-project contextvar AS THE REAL RESOLVER WOULD: it falls
             # back to this when no project_id is passed, so seeing it here proves
             # set_run_project ran first.
             "run_project_contextvar": mr.get_run_project(), **kwargs}
        )
        if raises is not None:
            raise raises
        return resolved if resolved is not None else _a_resolved_model()

    monkeypatch.setattr(dispatch, "resolve_model_for_run", _fake_resolve)

    class _FakeGraph:
        async def astream(self, state, stream_mode=None, config=None):
            recorder.log.append("graph")
            recorder.model_at_graph = mr.get_resolved_model()
            recorder.project_at_graph = mr.get_run_project()
            yield (type("M", (), {"content": "hi"})(), {})

        async def ainvoke(self, state, config=None):
            recorder.log.append("graph")
            recorder.model_at_graph = mr.get_resolved_model()
            recorder.project_at_graph = mr.get_run_project()
            return {"final_user_message": "hi"}

    def _load_graph():
        recorder.graph_loads += 1
        recorder.log.append("load_graph")
        return _FakeGraph()

    monkeypatch.setitem(
        reg.REGISTRY, agent_id,
        reg.AgentCapability(agent_id=agent_id, load_graph=_load_graph,
                            load_prompt=lambda: "SYS", mode=mode),
    )


async def _turn(agent_id="design", *, project_id="proj-A", tenant_id="t1",
                model_id=None, offering_id=None):
    return [e async for e in dispatch.run_agent(
        agent_id, text="hi", run_id="run-1", tenant_id=tenant_id,
        model_id=model_id, offering_id=offering_id, project_id=project_id)]


# ── 1. the resolver is called, and called WITH the run's project ─────────────


@pytest.mark.asyncio
async def test_the_turn_resolves_a_model_at_all(monkeypatch):
    """The whole gap in one assertion: orchestrator2 never called the resolver, so
    every agent fell through to the env key."""
    rec = _Recorder()
    _install(monkeypatch, rec)

    await _turn()

    assert rec.resolve_calls, (
        "the turn invoked no model resolution at all — every agent in the graph will "
        "fall back to the platform env key"
    )


@pytest.mark.asyncio
async def test_the_resolver_receives_the_runs_project_id_not_none(monkeypatch):
    """`None` here is not a weaker check, it is no check: the project's grant is
    skipped and the project's budget is never consulted."""
    rec = _Recorder()
    _install(monkeypatch, rec)

    await _turn(project_id="proj-A")

    call = rec.resolve_calls[0]
    assert call.get("project_id") is not None, (
        "resolve_model_for_run was called without a project_id — effective_project_"
        "offerings and check_budgets are both bypassed"
    )
    assert call["project_id"] == "proj-A"


@pytest.mark.asyncio
async def test_the_model_and_offering_selected_by_the_run_are_passed_through(monkeypatch):
    rec = _Recorder()
    _install(monkeypatch, rec)

    await _turn(model_id="claude-x", offering_id="offering-7")

    call = rec.resolve_calls[0]
    assert call["requested_model_id"] == "claude-x"
    assert call["offering_id"] == "offering-7"
    assert call["tenant_id"] == "t1"


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id,mode", [("design", "stream"), ("testing", "invoke")])
async def test_both_dispatch_modes_resolve_before_running(monkeypatch, agent_id, mode):
    """`testing` is the one invoke-mode agent and it is the agent this bug was found
    on. A fix that only covers the stream branch fixes eight of nine."""
    rec = _Recorder()
    _install(monkeypatch, rec, agent_id=agent_id, mode=mode)

    await _turn(agent_id, project_id="proj-A")

    assert rec.resolve_calls and rec.resolve_calls[0]["project_id"] == "proj-A"
    assert rec.model_at_graph is not None


# ── 2. ordering: both contextvars are set BEFORE the graph is invoked ────────


@pytest.mark.asyncio
async def test_the_resolved_model_is_on_the_contextvar_before_the_graph_runs(monkeypatch):
    """Not "both happened" — ORDER. `build_llm` runs inside the graph's first node,
    so a resolution stashed after `astream` is a resolution nothing ever reads."""
    rec = _Recorder()
    resolved = _a_resolved_model(model="claude-project-a")
    _install(monkeypatch, rec, resolved=resolved)

    await _turn()

    assert rec.model_at_graph is resolved, (
        f"the graph ran with resolved model {rec.model_at_graph!r} — build_llm inside "
        f"it would have fallen back to the env key"
    )
    assert rec.log.index("resolve") < rec.log.index("graph")


@pytest.mark.asyncio
async def test_the_run_project_contextvar_is_set_before_resolution_and_still_set_in_the_graph(monkeypatch):
    """`set_run_project` is the fallback scope for every budget check made deeper in
    the stack — including each agent's OWN `resolve_model_for_run`, which threads no
    project id. Set after resolution it would be too late for the first check; unset
    inside the graph it would be useless to every later one."""
    rec = _Recorder()
    _install(monkeypatch, rec)

    await _turn(project_id="proj-A")

    assert rec.resolve_calls[0]["run_project_contextvar"] == "proj-A", (
        "set_run_project had not run when the model was resolved — an agent that "
        "re-resolves mid-graph gets no project scope"
    )
    assert rec.project_at_graph == "proj-A"


# ── 3. failing closed: an error event, never a fallback ─────────────────────


_PROTOCOL_EVENT_TYPES = {"agent.selected", "stream_chunk", "tool.call", "error", "stream_end"}


@pytest.mark.asyncio
async def test_a_project_without_a_grant_gets_an_error_not_a_fallback(monkeypatch):
    """`NoModelConfiguredError` is what a project whose grants exclude every offering
    gets from the resolver. It must reach the user as a typed failure."""
    rec = _Recorder()
    _install(monkeypatch, rec, raises=mr.NoModelConfiguredError(
        "tenant t1 has grants configured but none apply to this project"))

    events = await _turn(project_id="proj-ungranted")

    types = [e["type"] for e in events]
    assert types == ["agent.selected", "error", "stream_end"], types
    assert not [e for e in events if e["type"] == "stream_chunk"]


@pytest.mark.asyncio
async def test_no_model_configured_means_the_graph_is_never_invoked(monkeypatch):
    """A fallback would show up here as the graph running anyway."""
    rec = _Recorder()
    _install(monkeypatch, rec, raises=mr.NoModelConfiguredError("no provider"))

    await _turn()

    assert rec.graph_loads == 0, "the graph was loaded despite having no usable model"
    assert rec.model_at_graph == "<never invoked>", "the graph ran with no resolved model"
    assert "graph" not in rec.log


@pytest.mark.asyncio
async def test_a_failed_resolution_leaves_no_model_on_the_contextvar(monkeypatch):
    """A socket serves many turns in one context. If turn 2's project has no grant,
    turn 1's resolved model must not still be sitting on the contextvar for anything
    downstream to pick up."""
    rec = _Recorder()
    mr.set_resolved_model(_a_resolved_model(model="previous-turns-model"))
    _install(monkeypatch, rec, raises=mr.NoModelConfiguredError("no provider"))

    await _turn(project_id="proj-B")

    assert mr.get_resolved_model() is None, (
        "a previous turn's resolved model survived a failed resolution — another "
        "project's key is still live in this context"
    )


@pytest.mark.asyncio
async def test_model_not_enabled_produces_an_error_then_stream_end(monkeypatch):
    rec = _Recorder()
    _install(monkeypatch, rec, raises=mr.ModelNotEnabledError(
        "model offering 'offering-9' is not enabled for this org"))

    events = await _turn()

    assert [e["type"] for e in events] == ["agent.selected", "error", "stream_end"]
    assert rec.graph_loads == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [
    mr.NoModelConfiguredError("none configured"),
    mr.ModelNotEnabledError("not enabled"),
])
async def test_the_error_event_matches_the_frontend_contract(monkeypatch, exc):
    """A frame the Zod union rejects is a frame the browser drops — the failure would
    be invisible again. `error` carries message/detail, and `agent` only because the
    agent id DID resolve here (it is one of the nine)."""
    rec = _Recorder()
    _install(monkeypatch, rec, raises=exc)

    events = await _turn()
    error = next(e for e in events if e["type"] == "error")

    assert set(error) <= {"type", "message", "detail", "agent"}, sorted(error)
    assert error["agent"] in set(reg.AGENT_IDS)
    assert error["message"] and isinstance(error["message"], str)
    assert error["detail"] == str(exc)
    for e in events:
        assert e["type"] in _PROTOCOL_EVENT_TYPES


@pytest.mark.asyncio
async def test_the_error_names_the_problem_a_reader_can_act_on(monkeypatch):
    """"Model resolution failed" is not actionable. The message has to say a model is
    missing for THIS PROJECT and who fixes it."""
    rec = _Recorder()
    _install(monkeypatch, rec, raises=mr.NoModelConfiguredError("none configured"))

    events = await _turn()
    message = next(e for e in events if e["type"] == "error")["message"].lower()

    assert "model" in message
    assert "project" in message
    assert "administrator" in message


def _module_code(module) -> str:
    """A module's source with every docstring and every comment stripped.

    Assertions about what code DOES must not be satisfiable — or, as here, DEFEATED —
    by prose about it: `dispatch`'s own docstring names `ANTHROPIC_API_KEY` while
    explaining the fallback it refuses to have.
    """
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)  # ast.unparse drops comments outright


def test_dispatch_has_no_env_key_fallback_anywhere():
    """The fallback that hid this bug for a release. In a deployed environment there
    IS no platform key, so a local `local-env:anthropic` success is a lie about
    production. Source-level because the whole point is that no code path reaches it."""
    code = _module_code(dispatch)
    for banned in ("ANTHROPIC_API_KEY", "local-env", "ANTHROPIC_MODEL"):
        assert banned not in code, (
            f"{banned} must not appear in orchestrator2 dispatch — resolution fails "
            f"closed, with no platform fallback"
        )


def test_project_id_is_required_with_no_default(monkeypatch):
    """A default of None is how project scoping gets dropped silently at the next
    call site added. Omitting it must be a TypeError, not a quiet unscoped run."""
    param = inspect.signature(dispatch.run_agent).parameters["project_id"]
    assert param.default is inspect.Parameter.empty, (
        "run_agent(project_id=None) as a default lets a caller unscope a run without "
        "saying so"
    )
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


# ── 4. the socket: the project comes from the run row, never the client ─────


_A_RUN = "11111111-1111-1111-1111-111111111111"
_A_TENANT = "22222222-2222-2222-2222-222222222222"


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
        if not self._inbound:
            raise WebSocketDisconnect()
        return self._inbound.pop(0)

    async def send_text(self, raw):
        self.sent.append(json.loads(raw))


def _patch_auth(monkeypatch, ws):
    async def _fake_redeem(ticket):
        return {"user_id": "u1", "tenant_id": "t1"}

    async def _fake_role(user_id, tenant_id):
        return "project_admin"

    monkeypatch.setattr(ws, "_redeem_ws_ticket", _fake_redeem)
    monkeypatch.setattr(ws, "_resolve_platform_role", _fake_role)


@pytest.mark.asyncio
async def test_the_socket_passes_the_runs_project_not_the_clients(monkeypatch):
    """A client-named project would let a Project Admin borrow a project whose grant
    includes a model theirs does not, and whose budget still has room. The socket
    already refuses a client-named run and a client-named model for the same reason."""
    from agents_orchestrator.orchestrator2 import ws

    _patch_auth(monkeypatch, ws)
    calls = []

    async def _fake_run_agent(agent_id, **kwargs):
        calls.append(kwargs)
        yield {"type": "stream_end"}

    monkeypatch.setattr(ws, "run_agent", _fake_run_agent)

    async def _owned(run_id, tenant_id):
        return ws.RunSelection(model_id="claude-x", offering_id="offering-7",
                               project_id="project-of-the-run")

    monkeypatch.setattr(ws, "_resolve_run", _owned)

    socket = _FakeWebSocket(
        params={"ticket": "tkt"},
        inbound=[json.dumps({"type": "user_message", "text": "hi", "agent": "design",
                             "run_id": _A_RUN, "project_id": "attacker-chosen-project"})],
    )
    await ws.orchestrator2_ws(socket)

    assert len(calls) == 1
    assert calls[0]["project_id"] == "project-of-the-run", (
        "the turn was scoped to a project the client named — that is the grant and "
        "the budget of whichever project the caller picks"
    )


@pytest.mark.asyncio
async def test_resolve_run_reads_project_id_off_the_verified_run_row(monkeypatch):
    """Where the project actually comes from: the `runs` row already proven to belong
    to the caller's tenant."""
    from agents_orchestrator.orchestrator2 import ws

    project_uuid = uuid.UUID("33333333-3333-3333-3333-333333333333")

    class _Run:
        model_id = "claude-x"
        offering_id = "offering-7"
        project_id = project_uuid

    class _Result:
        def scalar_one_or_none(self):
            return _Run()

    class _Session:
        async def execute(self, stmt):
            return _Result()

    class _Ctx:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("shared.db.get_db_session_for_tenant", lambda tenant_id: _Ctx())

    selection = await ws._resolve_run(_A_RUN, _A_TENANT)

    assert selection.project_id == str(project_uuid), (
        "the run's project must be returned as text — grant sets and budget scope "
        "keys compare it as a string"
    )
    assert selection.model_id == "claude-x"
    assert selection.offering_id == "offering-7"


@pytest.mark.asyncio
async def test_a_run_with_no_project_yields_none_rather_than_a_guess(monkeypatch):
    """Nullable since migration 0005 (webhook runs). Passing `None` through honestly
    fails closed on a governed tenant, which is correct — an ungoverned run is not a
    licence to use every model in the org."""
    from agents_orchestrator.orchestrator2 import ws

    class _Run:
        model_id = None
        offering_id = None
        project_id = None

    class _Result:
        def scalar_one_or_none(self):
            return _Run()

    class _Session:
        async def execute(self, stmt):
            return _Result()

    class _Ctx:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("shared.db.get_db_session_for_tenant", lambda tenant_id: _Ctx())

    selection = await ws._resolve_run(_A_RUN, _A_TENANT)
    assert selection.project_id is None
