"""The project's connector must be bound for the turn, or the agents cannot reach it.

THE BUG THIS FILE EXISTS FOR. The Development agent, asked to pull code from Azure
DevOps on a project with the ADO connector connected, answered:

    "It looks like Azure DevOps credentials aren't configured yet. To connect to ADO,
     you'll need to set up the ADO connector."

It was telling the truth about what it could see. `git_tools._active_ado_creds()`
resolves credentials ONLY from `config.connectors.context` — its own docstring says
"With no connector bound, this returns ("", "") — there is no process-wide PAT to
fall back on" — and `set_connector` appeared ZERO times in the whole `orchestrator2`
package. The old engine binds it in `copilot_api._stage_connector`, whose docstring
names this exact symptom: "nothing else in the Copilot path sets it, so without this
the agent reports 'no board connected' even when a connector IS selected."

This is the risk D10a recorded when it chose to reuse the agents' compiled graphs and
skip their `*_agent_api.py` wrappers: "if some agent's graph turns out to depend on
state its wrapper used to provide, that agent needs an adapter." It did.

THE SECOND HALF, and the reason binding alone is not enough. The credential for this
project lives in `project_integration_credentials` — a PROJECT-SCOPED PERSONAL
credential, keyed by `owner_id`. Resolved with `owner_id=""` the connector comes back
with no PAT at all, which is indistinguishable from having no connector. Measured
against the real dev tenant: `owner_id=""` gives `pat_present=False`; the project
admin's own id gives `org='https://dev.azure.com/srk02804', pat_present=True`.

So the turn's user is load-bearing, and `user_id` is keyword-required with no default
for the same reason `project_id` is: a default would silently reintroduce this bug.
"""
import inspect

import pytest
from langchain_core.messages import AIMessageChunk

from agents_orchestrator.orchestrator2 import dispatch, registry as reg
from shared.services import model_resolver as mr

_TENANT = "dfee0d2f-345e-430e-8084-7ab7276cc5b8"
_PROJECT = "c3b0cd34-6657-4f91-b1f2-04f394502f81"
_USER = "af57932d-f4f2-4673-aef7-d33b75c022f4"


@pytest.fixture(autouse=True)
def _stub_model_resolution(monkeypatch):
    async def _fake_resolve(tenant_id, requested_model_id=None, **kwargs):
        return mr.ResolvedModel(
            provider="anthropic", litellm_provider="anthropic", model="m",
            api_key="k", base_url=None, alias="tenant:t1:p1",
        )

    monkeypatch.setattr(dispatch, "resolve_model_for_run", _fake_resolve)
    mr.set_resolved_model(None)
    mr.set_run_project(None)
    yield
    mr.set_resolved_model(None)
    mr.set_run_project(None)


class _SeeingGraph:
    """A graph that records what the connector contextvar held while it ran.

    The assertion has to be made FROM INSIDE the graph. Checking after the turn would
    pass against an implementation that binds and clears without the agent ever
    seeing it, and checking before would pass against one that never binds at all.
    """

    def __init__(self, sink):
        self._sink = sink

    async def astream(self, state, stream_mode=None, config=None):
        from config.connectors.context import get_connector
        try:
            self._sink.append(get_connector())
        except Exception as exc:  # noqa: BLE001 — "nothing bound" is a result too
            self._sink.append(exc)
        yield (AIMessageChunk(content="done"), {})


def _install(monkeypatch, graph):
    monkeypatch.setitem(
        reg.REGISTRY, "development",
        reg.AgentCapability(agent_id="development", load_graph=lambda: graph,
                            load_prompt=lambda: "SYS", mode="stream"),
    )


async def _turn(**overrides):
    kwargs = dict(
        text="pull code from ADO", run_id="r1", tenant_id=_TENANT,
        model_id=None, offering_id=None, project_id=_PROJECT,
        user_id=_USER, context="", reason="",
    )
    kwargs.update(overrides)
    return [e async for e in dispatch.run_agent("development", **kwargs)]


# ── the bug itself ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_agent_can_see_a_connector_while_it_runs(monkeypatch):
    """The whole bug in one assertion: the graph saw nothing bound."""
    seen = []
    _install(monkeypatch, _SeeingGraph(seen))

    sentinel = object()

    async def _fake_resolve_connector(**kwargs):
        return sentinel

    monkeypatch.setattr(
        "agents_orchestrator.orchestrator2.connectors._resolve_connector",
        _fake_resolve_connector,
    )
    await _turn()
    assert seen and seen[0] is sentinel, (
        "the agent's tools read the connector off config.connectors.context; nothing "
        "bound means every board/repo tool reports 'not configured'"
    )


@pytest.mark.asyncio
async def test_the_connector_is_resolved_for_this_project_and_this_user(monkeypatch):
    """`project_id` scopes access; `owner_id` finds the project-scoped PERSONAL
    credential. Without the latter the PAT is empty and the agent reports exactly the
    same 'not configured' as having no connector at all."""
    _install(monkeypatch, _SeeingGraph([]))
    captured = {}

    async def _fake_resolve_connector(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        "agents_orchestrator.orchestrator2.connectors._resolve_connector",
        _fake_resolve_connector,
    )
    await _turn()
    assert captured.get("tenant_id") == _TENANT
    assert captured.get("project_id") == _PROJECT
    assert captured.get("owner_id") == _USER, (
        "the turn's user is what finds a project-scoped personal credential"
    )
    assert captured.get("agent_id") == "development", (
        "the agent id IS the access decision — the level lives per (agent, tool)"
    )


# ── credentials must not outlive the turn ────────────────────────────────────


@pytest.mark.asyncio
async def test_the_connector_is_cleared_when_the_turn_ends(monkeypatch):
    """A connector carries live credentials. Leaving it bound would let the NEXT
    turn — possibly another project's — run against this one's PAT."""
    from config.connectors.context import get_connector

    _install(monkeypatch, _SeeingGraph([]))

    async def _fake_resolve_connector(**kwargs):
        return object()

    monkeypatch.setattr(
        "agents_orchestrator.orchestrator2.connectors._resolve_connector",
        _fake_resolve_connector,
    )
    await _turn()
    with pytest.raises(Exception):
        get_connector()


@pytest.mark.asyncio
async def test_the_connector_is_cleared_even_when_the_turn_fails(monkeypatch):
    from config.connectors.context import get_connector

    class _Exploding:
        async def astream(self, state, stream_mode=None, config=None):
            raise RuntimeError("graph died")
            yield  # pragma: no cover

    _install(monkeypatch, _Exploding())

    async def _fake_resolve_connector(**kwargs):
        return object()

    monkeypatch.setattr(
        "agents_orchestrator.orchestrator2.connectors._resolve_connector",
        _fake_resolve_connector,
    )
    events = await _turn()
    assert [e for e in events if e["type"] == "error"]
    with pytest.raises(Exception):
        get_connector()


# ── failing to resolve one must not kill the turn ────────────────────────────


@pytest.mark.asyncio
async def test_a_connector_that_cannot_be_resolved_leaves_the_turn_alive(monkeypatch):
    """A project with no connector configured is a normal state, not an error. The
    agent then says it cannot reach ADO — which is TRUE, and is the one case where
    that message is the right answer."""
    seen = []
    _install(monkeypatch, _SeeingGraph(seen))

    async def _boom(**kwargs):
        raise RuntimeError("no connector for this project")

    monkeypatch.setattr(
        "agents_orchestrator.orchestrator2.connectors._resolve_connector", _boom
    )
    events = await _turn()
    types = [e["type"] for e in events]
    assert types[-1] == "stream_end"
    assert "stream_chunk" in types, "the agent must still get to answer"


# ── the kind comes from the project, not from a guess ────────────────────────


@pytest.mark.asyncio
async def test_the_connector_kind_comes_from_the_projects_own_selection(monkeypatch):
    from agents_orchestrator.orchestrator2 import connectors

    async def _fake_map(tenant_id, project_id):
        return {"development": ["jira", "azure_devops"]}

    monkeypatch.setattr(connectors, "_project_connectors", _fake_map)
    kind = await connectors.connector_kind_for(
        "development", tenant_id=_TENANT, project_id=_PROJECT
    )
    assert kind == "jira", "the project's first selected kind wins"


@pytest.mark.asyncio
async def test_the_kind_falls_back_to_azure_devops_when_unselected(monkeypatch):
    """Existing projects made no per-agent selection, and the platform's default is
    azure_devops. Matching `stage_connector_kind`'s own fallback keeps the two
    surfaces agreeing about what a project with no selection means."""
    from agents_orchestrator.orchestrator2 import connectors

    async def _empty(tenant_id, project_id):
        return {}

    monkeypatch.setattr(connectors, "_project_connectors", _empty)
    kind = await connectors.connector_kind_for(
        "development", tenant_id=_TENANT, project_id=_PROJECT
    )
    assert kind == "azure_devops"


# ── the argument that must never acquire a default ───────────────────────────


def test_user_id_is_keyword_required_with_no_default():
    """Same reasoning as `project_id`, and now with a demonstrated cost: a default of
    "" resolves a connector with no PAT, which the agent reports as 'credentials
    aren't configured' — the exact bug, silently reintroduced by a call site that
    forgot an argument."""
    sig = inspect.signature(dispatch.run_agent)
    param = sig.parameters["user_id"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty
