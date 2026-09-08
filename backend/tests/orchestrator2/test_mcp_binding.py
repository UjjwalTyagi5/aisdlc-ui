"""The project's MCP servers must be bound for the turn, like its connector.

THE SAME GAP AS THE CONNECTOR, one wrapper over. The old engine wraps every turn in
TWO context managers:

    async with _stage_connector(shim_input, active, tenant_id, owner_id=user_id):
        async with mcp_tools_for_stage(shim_input, active, owner_id=user_id):

`orchestrator2` had neither. The connector one was found because the Development agent
said out loud that it could not reach Azure DevOps; MCP would never have said anything
— the agent node binds whatever `mcp_runtime` holds, so an unbound contextvar is an
agent with fewer tools and no complaint. A project that registers MCP servers would
simply find the Orchestrator quietly ignoring them.

That silence is the reason this is worth a test rather than a bug report later: it is
the same "declared interface with no data behind it" shape that left the Activity tab
wired to an empty array through an entire 638-test suite.
"""
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
            api_key="k", base_url=None, alias="a",
        )

    monkeypatch.setattr(dispatch, "resolve_model_for_run", _fake_resolve)
    mr.set_resolved_model(None)
    mr.set_run_project(None)
    yield
    mr.set_resolved_model(None)
    mr.set_run_project(None)


@pytest.fixture(autouse=True)
def _no_connector(monkeypatch):
    """This file is about MCP; the connector has its own file."""
    async def _none(**kwargs):
        raise RuntimeError("no connector in this test")

    monkeypatch.setattr(
        "agents_orchestrator.orchestrator2.connectors._resolve_connector", _none
    )


class _SeeingGraph:
    """Records what `mcp_runtime` held while the graph ran.

    Asserted from INSIDE the graph on purpose. Checking afterwards would pass against
    an implementation that binds and clears without the agent ever seeing the tools.
    """

    def __init__(self, sink):
        self._sink = sink

    async def astream(self, state, stream_mode=None, config=None):
        from shared.tools.mcp_runtime import get_mcp_tools
        self._sink.append(get_mcp_tools())
        yield (AIMessageChunk(content="ok"), {})


def _install(monkeypatch, graph, agent="development"):
    monkeypatch.setitem(
        reg.REGISTRY, agent,
        reg.AgentCapability(agent_id=agent, load_graph=lambda: graph,
                            load_prompt=lambda: "SYS", mode="stream"),
    )


async def _turn(agent="development", **overrides):
    kwargs = dict(
        text="hi", run_id="r1", tenant_id=_TENANT, model_id=None, offering_id=None,
        project_id=_PROJECT, user_id=_USER, context="", reason="",
    )
    kwargs.update(overrides)
    return [e async for e in dispatch.run_agent(agent, **kwargs)]


@pytest.mark.asyncio
async def test_the_agent_sees_the_projects_mcp_tools_while_it_runs(monkeypatch):
    """The gap itself: the agent node binds whatever mcp_runtime holds, so nothing
    bound is an agent silently missing every tool the project registered."""
    from agents_orchestrator.orchestrator2 import mcp

    seen = []
    _install(monkeypatch, _SeeingGraph(seen))

    sentinel = ["tool-a", "tool-b"]

    async def _fake_maps(tenant_id, project_id):
        return ({}, {"development": ["srv-1"]})

    async def _fake_load(tenant_id, server_ids, agent_id, project_id, owner_id):
        return sentinel

    monkeypatch.setattr(mcp, "_project_maps", _fake_maps)
    monkeypatch.setattr(mcp, "_load_tools", _fake_load)
    await _turn()
    assert seen and seen[0] == sentinel


@pytest.mark.asyncio
async def test_the_servers_are_this_projects_selection_for_this_agent(monkeypatch):
    """`mcp_servers` is a per-agent map. Handing an agent another agent's servers
    would give it tools the project never granted it."""
    from agents_orchestrator.orchestrator2 import mcp

    _install(monkeypatch, _SeeingGraph([]))
    captured = {}

    async def _fake_maps(tenant_id, project_id):
        # The target agent is deliberately NOT first. Dicts preserve insertion order,
        # so an implementation that takes "the first selection in the map" instead of
        # this agent's own returns the right answer by accident when the agent under
        # test happens to lead — which is exactly what an earlier version of this
        # fixture did, and it let that mutation survive the whole suite.
        return ({}, {"security": ["sec-srv"], "development": ["dev-srv"]})

    async def _fake_load(tenant_id, server_ids, agent_id, project_id, owner_id):
        captured.update(tenant_id=tenant_id, server_ids=server_ids,
                        agent_id=agent_id, project_id=project_id, owner_id=owner_id)
        return []

    monkeypatch.setattr(mcp, "_project_maps", _fake_maps)
    monkeypatch.setattr(mcp, "_load_tools", _fake_load)
    await _turn()

    assert captured["server_ids"] == ["dev-srv"], "another agent's servers must not leak"
    assert captured["agent_id"] == "development"
    assert captured["tenant_id"] == _TENANT
    assert captured["project_id"] == _PROJECT
    assert captured["owner_id"] == _USER, (
        "project_id + owner_id let a server prefer this member's own saved credential "
        "over the org-registered one"
    )


@pytest.mark.asyncio
async def test_tools_do_not_outlive_the_turn(monkeypatch):
    """MCP tools can carry credentials of their own. Left bound they would be
    available to the next turn, on another agent and possibly another project."""
    from shared.tools.mcp_runtime import get_mcp_tools

    from agents_orchestrator.orchestrator2 import mcp

    _install(monkeypatch, _SeeingGraph([]))

    async def _fake_maps(tenant_id, project_id):
        return ({}, {"development": ["srv-1"]})

    async def _fake_load(tenant_id, server_ids, agent_id, project_id, owner_id):
        return ["tool-a"]

    monkeypatch.setattr(mcp, "_project_maps", _fake_maps)
    monkeypatch.setattr(mcp, "_load_tools", _fake_load)
    await _turn()
    assert get_mcp_tools() == []


@pytest.mark.asyncio
async def test_tools_are_cleared_even_when_the_turn_fails(monkeypatch):
    from shared.tools.mcp_runtime import get_mcp_tools

    from agents_orchestrator.orchestrator2 import mcp

    class _Exploding:
        async def astream(self, state, stream_mode=None, config=None):
            raise RuntimeError("graph died")
            yield  # pragma: no cover

    _install(monkeypatch, _Exploding())

    async def _fake_maps(tenant_id, project_id):
        return ({}, {"development": ["srv-1"]})

    async def _fake_load(tenant_id, server_ids, agent_id, project_id, owner_id):
        return ["tool-a"]

    monkeypatch.setattr(mcp, "_project_maps", _fake_maps)
    monkeypatch.setattr(mcp, "_load_tools", _fake_load)
    events = await _turn()
    assert [e for e in events if e["type"] == "error"]
    assert get_mcp_tools() == []


@pytest.mark.asyncio
async def test_a_project_with_no_mcp_selection_is_a_no_op(monkeypatch):
    """Most projects register none. That is an ordinary state, not a degraded one."""
    from agents_orchestrator.orchestrator2 import mcp

    seen = []
    _install(monkeypatch, _SeeingGraph(seen))
    loaded = []

    async def _fake_maps(tenant_id, project_id):
        return ({}, {})

    async def _fake_load(tenant_id, server_ids, agent_id, project_id, owner_id):
        loaded.append(server_ids)
        return []

    monkeypatch.setattr(mcp, "_project_maps", _fake_maps)
    monkeypatch.setattr(mcp, "_load_tools", _fake_load)
    events = await _turn()

    assert loaded == [], "no selection must not reach the loader at all"
    assert seen == [[]]
    assert [e["type"] for e in events][-1] == "stream_end"


@pytest.mark.asyncio
async def test_an_mcp_failure_never_fails_the_turn(monkeypatch):
    """An unreachable MCP server is an infrastructure problem somewhere else. The
    agent should still answer, with the tools it does have."""
    from agents_orchestrator.orchestrator2 import mcp

    seen = []
    _install(monkeypatch, _SeeingGraph(seen))

    async def _fake_maps(tenant_id, project_id):
        return ({}, {"development": ["srv-1"]})

    async def _boom(tenant_id, server_ids, agent_id, project_id, owner_id):
        raise RuntimeError("mcp server unreachable")

    monkeypatch.setattr(mcp, "_project_maps", _fake_maps)
    monkeypatch.setattr(mcp, "_load_tools", _boom)
    events = await _turn()

    types = [e["type"] for e in events]
    assert types[-1] == "stream_end"
    assert "stream_chunk" in types, "the agent must still get to answer"
    assert seen == [[]], "and with no MCP tools, rather than a half-loaded set"
