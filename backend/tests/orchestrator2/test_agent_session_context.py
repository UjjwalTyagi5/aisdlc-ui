"""The agents' own per-run context: session id, user, and connector kind.

THE BUG THIS FILE EXISTS FOR. The Deliverables tab showed "Repository code" with
"Waiting for the Development agent to pull the repository…" and "No files yet" — after
the agent had cloned the repo and reported success in chat.

The Development agent's tools do not take a working directory. They build one from
`get_session_id()` (`config/ws_helper`), and `orchestrator2` never called
`set_session_id`, so it returned None: the clone went somewhere keyed on nothing, while
`runs.py::_run_dev_work_dir` looked under the RUN id — its docstring says "the dev agent
clones into a run-keyed workspace (session_id == run_id)", which was true of the
standalone wrapper and not of this engine.

`set_user_id` and `set_provider_kind` were missing for the same reason. The work_dir
glob is `files/<user>/orchestrator/<run_id>/project`, so without the user the path is
wrong even once the session id is right; and the provider kind is what tells the repo
tools they are talking to Azure DevOps rather than GitHub.

This is the third instance of the same shape — after the connector and the MCP tools —
and it is exactly the cost D10a wrote down: reuse the compiled graphs, and the state
their `*_agent_api.py` wrappers used to set has to be set here instead.
"""
import pytest
from langchain_core.messages import AIMessageChunk

from agents_orchestrator.orchestrator2 import dispatch, registry as reg
from shared.services import model_resolver as mr

_RUN = "run-abc-123"
_TENANT = "22222222-2222-2222-2222-222222222222"
_PROJECT = "33333333-3333-3333-3333-333333333333"
_USER = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(autouse=True)
def _stub_model_resolution(monkeypatch):
    async def _fake_resolve(tenant_id, requested_model_id=None, **kwargs):
        return mr.ResolvedModel(
            provider="anthropic", litellm_provider="anthropic", model="m",
            api_key="k", base_url=None, alias="a",
        )

    monkeypatch.setattr(dispatch, "resolve_model_for_run", _fake_resolve)
    mr.set_resolved_model(None)
    mr.set_run_project(None)
    yield
    mr.set_resolved_model(None)
    mr.set_run_project(None)


class _SeeingGraph:
    """Records the agent-facing context as the graph sees it, from inside the run."""

    def __init__(self, sink):
        self._sink = sink

    async def astream(self, state, stream_mode=None, config=None):
        from config.ws_helper import get_session_id, get_user_id
        self._sink.append({
            "session_id": get_session_id(),
            "user_id": get_user_id(),
        })
        yield (AIMessageChunk(content="ok"), {})


def _install(monkeypatch, graph, agent="development"):
    monkeypatch.setitem(
        reg.REGISTRY, agent,
        reg.AgentCapability(agent_id=agent, load_graph=lambda: graph,
                            load_prompt=lambda: "SYS", mode="stream"),
    )


async def _turn(agent="development", **overrides):
    kwargs = dict(
        text="pull code from ADO", run_id=_RUN, tenant_id=_TENANT, model_id=None,
        offering_id=None, project_id=_PROJECT, user_id=_USER, context="", reason="",
    )
    kwargs.update(overrides)
    return [e async for e in dispatch.run_agent(agent, **kwargs)]


@pytest.mark.asyncio
async def test_the_session_id_the_agent_sees_is_the_run_id(monkeypatch):
    """The whole bug in one assertion.

    `_run_dev_work_dir` resolves the clone by run id. If the agent cloned under a
    different session id — or None — the repository is on disk and the panel cannot
    find it, which is indistinguishable from a pull that never happened.
    """
    seen = []
    _install(monkeypatch, _SeeingGraph(seen))
    await _turn()
    assert seen and seen[0]["session_id"] == _RUN


@pytest.mark.asyncio
async def test_the_agent_knows_whose_turn_it_is(monkeypatch):
    """The work_dir glob is `files/<user>/orchestrator/<run_id>/project`, so without the
    user the path is wrong even when the session id is right."""
    seen = []
    _install(monkeypatch, _SeeingGraph(seen))
    await _turn()
    assert seen and seen[0]["user_id"] == _USER


@pytest.mark.asyncio
async def test_the_context_does_not_outlive_the_turn(monkeypatch):
    """It names a run and a person. Left set, the next turn on this socket — possibly
    another run — would clone into the previous one's directory."""
    from config.ws_helper import get_session_id

    _install(monkeypatch, _SeeingGraph([]))
    await _turn()
    assert get_session_id() != _RUN


@pytest.mark.asyncio
async def test_it_is_cleared_even_when_the_turn_fails(monkeypatch):
    from config.ws_helper import get_session_id

    class _Exploding:
        async def astream(self, state, stream_mode=None, config=None):
            raise RuntimeError("graph died")
            yield  # pragma: no cover

    _install(monkeypatch, _Exploding())
    events = await _turn()
    assert [e for e in events if e["type"] == "error"]
    assert get_session_id() != _RUN


@pytest.mark.asyncio
async def test_every_agent_gets_it_not_just_development(monkeypatch):
    """Requirements and Testing write generated files under the same per-user, per-run
    tree that the file-tree pointers browse. Scoping this to Development would leave
    those empty for the same reason."""
    seen = []
    _install(monkeypatch, _SeeingGraph(seen), agent="requirements")
    await _turn(agent="requirements")
    assert seen and seen[0]["session_id"] == _RUN
