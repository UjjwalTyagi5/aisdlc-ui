"""Choosing a board by name, from a live failure where the agent could not.

A user asked the Requirements agent to "create any of them but on ado not on jira".
The agent said it would — and could not. The board connector is resolved once per turn
from `connectors["requirements"]`, Jira was first in that list, and no tool took an
argument naming a board. So the agent would have written to Jira while telling the user
it was using Azure DevOps: the worst of the available outcomes, because it is wrong and
confident at the same time.

THE SECURITY PROPERTY THAT MADE THIS SAFE TO ADD. Naming a provider chooses between
boards the project ALREADY holds. It resolves through `get_connector_for_session` with
the project, the stage and the owner, exactly as `agent_run_scope` does — so the level
is looked up per (stage, tool) for that provider, and a board the stage never wired
resolves to no access rather than to the other board. The argument widens what the agent
can SAY, not what the project can REACH.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TENANT = "11111111-1111-1111-1111-111111111111"
PROJECT = "22222222-2222-2222-2222-222222222222"
USER = "the-ba"


# ── what a person calls each board ───────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("said", "kind"),
    [
        ("ado", "azure_devops"),
        ("ADO", "azure_devops"),
        ("Azure DevOps", "azure_devops"),
        ("azure-devops", "azure_devops"),
        ("azure_devops", "azure_devops"),
        ("devops", "azure_devops"),
        ("jira", "jira"),
        ("Jira", "jira"),
        ("  JIRA  ", "jira"),
    ],
)
def test_the_names_people_actually_use_resolve(said, kind):
    from agents_orchestrator.requirements_agent.agents.planning import _canonical_provider

    assert _canonical_provider(said) == kind


@pytest.mark.unit
@pytest.mark.parametrize("said", ["", "  ", "trello", "notion", "the board"])
def test_an_unknown_name_resolves_to_nothing_rather_than_a_guess(said):
    """Guessing here writes to the wrong board. Empty means "tell the user what
    exists", which `_named_board_connector` then does."""
    from agents_orchestrator.requirements_agent.agents.planning import _canonical_provider

    assert _canonical_provider(said) == ""


# ── it cannot reach a board the stage does not hold ──────────────────────────


def _ctx(tenant=TENANT, project=PROJECT, user=USER):
    import config.ws_helper as ws

    return (
        patch.object(ws, "get_tenant_id", lambda: tenant),
        patch.object(ws, "get_project_id", lambda: project),
        patch.object(ws, "get_user_id", lambda: user),
    )


async def _named(provider, *, boards, factory=None, **ctx):
    from agents_orchestrator.requirements_agent.agents import planning

    factory = factory or AsyncMock(return_value=object())
    a, b, c = _ctx(**ctx)
    with a, b, c, \
            patch.object(planning, "_stage_boards", AsyncMock(return_value=boards)), \
            patch("config.connector_factory.get_connector_for_session", factory):
        return await planning._named_board_connector(provider), factory


@pytest.mark.unit
async def test_a_board_the_stage_never_wired_is_refused():
    """THE HEADLINE. The stage holds Jira only; asking for ADO must refuse and say what
    IS available — not silently fall through to Jira, which is what the agent would
    effectively have done before."""
    (conn, err), factory = await _named("ado", boards=["jira"])
    assert conn is None
    assert "not wired to azure_devops" in err
    assert "jira" in err
    factory.assert_not_awaited()  # never even attempted


@pytest.mark.unit
async def test_an_unrecognised_board_name_lists_what_exists():
    (conn, err), factory = await _named("trello", boards=["jira", "azure_devops"])
    assert conn is None
    assert "not a board this agent recognises" in err
    assert "jira" in err and "azure_devops" in err
    factory.assert_not_awaited()


@pytest.mark.unit
async def test_a_wired_board_resolves_through_the_normal_access_path():
    """Every argument that makes the access check work must be present — the same set
    agent_run_scope passes. Miss any one and the level resolves to nothing (agent_id),
    or the personal credential is never found (owner_id)."""
    (conn, err), factory = await _named("ado", boards=["jira", "azure_devops"])
    assert err is None and conn is not None
    kwargs = factory.await_args.kwargs
    assert kwargs["kind"] == "azure_devops"
    assert kwargs["agent_id"] == "requirements"
    assert kwargs["project_id"] == PROJECT
    assert kwargs["owner_id"] == USER
    assert kwargs["tenant_id"] == TENANT


@pytest.mark.unit
async def test_a_run_with_no_project_falls_back_rather_than_resolving_nothing():
    """A queued run has no project in context. Resolving a provider there would yield a
    connector permitting nothing; deferring to the injected one is the honest answer."""
    (conn, err), factory = await _named("ado", boards=["azure_devops"], project=None)
    assert conn is None and err == ""  # empty error == "fall through", not a refusal
    factory.assert_not_awaited()


# ── the choke point honours it ───────────────────────────────────────────────


@pytest.mark.unit
async def test_naming_a_provider_overrides_the_injected_connector():
    from agents_orchestrator.requirements_agent.agents import planning

    named = _Board("Azure Boards", "azure_devops")
    injected = _Board("Jira", "jira")
    with patch.object(planning, "_named_board_connector",
                      AsyncMock(return_value=(named, None))), \
            patch.object(planning, "_get_active_connector", lambda: injected):
        conn, err = await planning._board_connector("read", provider="ado")
    assert err is None
    assert conn is named


@pytest.mark.unit
async def test_omitting_the_provider_uses_the_stage_default():
    """The overwhelmingly common path must be untouched by this feature."""
    from agents_orchestrator.requirements_agent.agents import planning

    injected = _Board("Jira", "jira")
    resolver = AsyncMock()
    with patch.object(planning, "_named_board_connector", resolver), \
            patch.object(planning, "_get_active_connector", lambda: injected):
        conn, err = await planning._board_connector("read")
    assert err is None and conn is injected
    resolver.assert_not_awaited()


@pytest.mark.unit
async def test_a_refused_provider_stops_the_write_before_the_tier_check():
    """The refusal must come back as a plain string, the same shape as "no board
    connected" — not as an exception a tool turns into "the board rejected it", which
    an LLM retries."""
    from agents_orchestrator.requirements_agent.agents import planning

    with patch.object(planning, "_named_board_connector",
                      AsyncMock(return_value=(None, "not wired to azure_devops"))), \
            patch.object(planning, "_get_active_connector", lambda: _Board("Jira", "jira")):
        conn, err = await planning._board_connector("write", provider="ado")
    assert conn is None
    assert err == "not wired to azure_devops"


# ── every board tool takes it, and the model can discover the names ──────────


@pytest.mark.unit
def test_every_board_tool_accepts_a_provider():
    """A tool that silently ignored `provider` would write to the wrong board while
    reporting the right one — the exact failure this feature exists to remove."""
    import inspect

    from agents_orchestrator.requirements_agent.agents import planning

    for tool in planning._BOARD_TOOLS:
        src = inspect.getsource(tool.func or tool.coroutine)
        if "_board_connector(" not in src:
            continue
        props = (tool.args_schema.model_json_schema().get("properties") or {})
        assert "provider" in props, f"{tool.name} cannot be told which board to use"
        assert "provider" in src.split("_board_connector(", 1)[1][:60], (
            f"{tool.name} accepts provider but does not pass it on"
        )


@pytest.mark.unit
def test_the_discovery_tool_is_registered():
    from agents_orchestrator.requirements_agent.agents import planning

    assert "list_board_providers" in {t.name for t in planning._BOARD_TOOLS}


@pytest.mark.unit
async def test_the_discovery_tool_says_when_there_is_no_board():
    from agents_orchestrator.requirements_agent.agents import planning

    with patch.object(planning, "_stage_boards", AsyncMock(return_value=[])):
        out = await planning.list_board_providers.ainvoke({})
    assert "no board connected" in out


@pytest.mark.unit
async def test_the_discovery_tool_lists_boards_and_names_the_default():
    from agents_orchestrator.requirements_agent.agents import planning

    with patch.object(planning, "_stage_boards",
                      AsyncMock(return_value=["azure_devops", "jira"])):
        out = await planning.list_board_providers.ainvoke({})
    assert "azure_devops" in out and "jira" in out
    assert "(default)" in out


class _Board:
    """A bare connector: no `access_level`, so the lattice check is not what is under
    test here."""

    def __init__(self, display_name: str, connector_name: str):
        self.display_name = display_name
        self.connector_name = connector_name
