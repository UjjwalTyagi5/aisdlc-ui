"""Every connector acquisition must name the stage it is for.

THE BUG THIS LOCKS OUT. Since migration 0024 the access level is stored per
(stage, tool). `effective_access` therefore returns None for a caller that names no
stage — "a request that belongs to no stage cannot be checked against one". None then
means `ScopedConnector(raw, None)`, and `permits(None, mode)` is False for every mode.

So a worker that passed `project_id` without `agent_id` got a connector that permitted
nothing, no matter what an administrator had granted. Every board tool in every
worker-driven run answered "this project does not have READ access to its board" —
which is a plausible enough sentence that the real cause hid behind it for a long time.

It fails in the SAFE direction, which is exactly why it needs a test: nothing breaks
loudly, the feature is just quietly dead. `copilot_api.py` already passes `agent_id`
with a comment saying why; the three workers, `agent_run_scope` and the Design agent's
Figma tool are the sites that were left behind.

Naming the stage LOOSENS nothing. A connector that named no stage permitted nothing at
all; after the fix it permits exactly what the grant says, which may still be nothing.

The second half is the board KIND. It was hardcoded "azure_devops" in all three
workers, so a tenant whose stage is wired to Jira had its grant looked up under
`target_ref="azure_devops"` — found nothing, and never touched the Jira board it had
actually connected.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.authz.connector_access import permits  # noqa: E402

WORKERS = [
    ("workers.requirements_worker", "RequirementsWorker", "requirements",
     "agents_orchestrator.requirements_agent.agents.planning"),
    ("workers.design_worker", "DesignWorker", "design",
     "agents_orchestrator.design_architecture_agent.agents.architecture"),
    ("workers.development_worker", "DevelopmentWorker", "development",
     "agents_orchestrator.development_agent.agents.dev_agent"),
]

TENANT = "11111111-1111-1111-1111-111111111111"
PROJECT = "22222222-2222-2222-2222-222222222222"


def _fields(project_id: str = PROJECT) -> dict:
    """A task as redis-py 7.x hands it over — bytes keys and values."""
    import json

    return {
        b"run_id": b"run-1",
        b"tenant_id": TENANT.encode(),
        b"payload": json.dumps({"project_id": project_id}).encode(),
    }


class _StubGraph:
    def __init__(self):
        self.invoked = False

    async def ainvoke(self, payload, config):
        self.invoked = True


def _stub_graph_module(module_path: str, graph: _StubGraph):
    """Stand in for the agent graph module the worker imports lazily.

    The real ones pull in LangGraph, every tool module and a checkpointer. This test is
    about the two arguments passed to the connector factory, and nothing about that
    needs a compiled graph.
    """
    mod = types.ModuleType(module_path)
    mod.app = graph
    return patch.dict(sys.modules, {module_path: mod})


async def _run_worker(worker_module, cls_name, graph_module, *, kind="jira"):
    """Drive one task through a worker, capturing the connector-factory call."""
    import importlib

    mod = importlib.import_module(worker_module)
    worker = object.__new__(getattr(mod, cls_name))  # skip __init__: it wants Redis

    factory = AsyncMock(return_value=object())
    graph = _StubGraph()
    with _stub_graph_module(graph_module, graph), \
            patch.object(mod, "get_connector_for_session", factory), \
            patch.object(mod, "_stage_board_kind", AsyncMock(return_value=kind)), \
            patch.object(mod, "set_connector"), patch.object(mod, "clear_connector"):
        await worker.handle_task(_fields())
    return factory, graph


# ── the stage must be named ──────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("worker_module,cls_name,stage,graph_module", WORKERS)
async def test_the_worker_names_its_stage_when_acquiring_a_connector(
    worker_module, cls_name, stage, graph_module
):
    factory, _ = await _run_worker(worker_module, cls_name, graph_module)
    kwargs = factory.await_args.kwargs
    assert kwargs.get("agent_id") == stage, (
        "without agent_id the level resolves to None and the connector permits nothing"
    )
    assert kwargs.get("project_id") == PROJECT
    assert kwargs.get("tenant_id") == TENANT


@pytest.mark.unit
@pytest.mark.parametrize("worker_module,cls_name,stage,graph_module", WORKERS)
async def test_the_stage_matches_the_key_the_grant_is_stored_under(
    worker_module, cls_name, stage, graph_module
):
    """`projects.connectors` is `{agent_id: [target_ref]}`. A worker whose AGENT_ID
    does not match a key the stage picker writes finds no wiring and gets no access —
    the same dead end as omitting it, so the constant itself is worth pinning."""
    import importlib

    assert getattr(importlib.import_module(worker_module), cls_name).AGENT_ID == stage


# ── the board kind must come from the project, not a constant ────────────────


@pytest.mark.unit
@pytest.mark.parametrize("worker_module,cls_name,stage,graph_module", WORKERS)
async def test_the_board_kind_comes_from_what_the_stage_wired(
    worker_module, cls_name, stage, graph_module
):
    factory, _ = await _run_worker(worker_module, cls_name, graph_module, kind="jira")
    assert factory.await_args.kwargs.get("kind") == "jira", (
        "hardcoding azure_devops looks up the grant under a provider the tenant "
        "never connected"
    )


@pytest.mark.unit
@pytest.mark.parametrize("worker_module,cls_name,stage,graph_module", WORKERS)
async def test_no_board_wired_injects_nothing_rather_than_azure_devops(
    worker_module, cls_name, stage, graph_module
):
    """`_stage_board_kind` returns None on purpose — it refuses to fall back to the
    legacy `provider_kind` column, which defaults to azure_devops for nearly every
    project. Honouring that means acquiring no connector at all, so the board tools
    say "connect a board" instead of reporting a permission error about ADO."""
    factory, graph = await _run_worker(worker_module, cls_name, graph_module, kind=None)
    factory.assert_not_awaited()
    assert graph.invoked, "the run must still proceed on uploaded or pasted input"


@pytest.mark.unit
@pytest.mark.parametrize("worker_module,cls_name,stage,graph_module", WORKERS)
async def test_a_run_with_no_board_cannot_inherit_one(
    worker_module, cls_name, stage, graph_module
):
    """Injection is conditional now, so a no-board run must be cleared BEFORE it
    starts as well as after. Otherwise it inherits whatever connector the previous
    task on this context left behind, reads a different project's board, and looks
    like it worked."""
    import importlib

    mod = importlib.import_module(worker_module)
    worker = object.__new__(getattr(mod, cls_name))

    with _stub_graph_module(graph_module, _StubGraph()), \
            patch.object(mod, "get_connector_for_session", AsyncMock()), \
            patch.object(mod, "_stage_board_kind", AsyncMock(return_value=None)), \
            patch.object(mod, "set_connector") as setc, \
            patch.object(mod, "clear_connector") as clearc:
        await worker.handle_task(_fields())
    setc.assert_not_called()
    assert clearc.call_count >= 1, "a no-board run must be cleared before it starts"


@pytest.mark.unit
@pytest.mark.parametrize("worker_module,cls_name,stage,graph_module", WORKERS)
async def test_a_failing_run_still_releases_the_connector(
    worker_module, cls_name, stage, graph_module
):
    import importlib

    mod = importlib.import_module(worker_module)
    worker = object.__new__(getattr(mod, cls_name))

    class _Boom(_StubGraph):
        async def ainvoke(self, payload, config):
            raise RuntimeError("graph exploded")

    with _stub_graph_module(graph_module, _Boom()), \
            patch.object(mod, "get_connector_for_session", AsyncMock()), \
            patch.object(mod, "_stage_board_kind", AsyncMock(return_value="jira")), \
            patch.object(mod, "set_connector"), \
            patch.object(mod, "clear_connector") as clearc:
        with pytest.raises(RuntimeError):
            await worker.handle_task(_fields())
    clearc.assert_called_once()


# ── the chat path, which had the same omission ───────────────────────────────


@pytest.mark.unit
async def test_the_chat_path_also_names_its_stage():
    """`agent_run_scope` is the chat-turn equivalent of the worker, and it omitted
    `agent_id` too — so board tools were denied in chat as well as in queued runs."""
    from shared.services import agent_run

    factory = AsyncMock(return_value=object())
    with patch.object(agent_run, "get_connector_for_session", factory), \
            patch.object(agent_run, "_stage_board_kind", AsyncMock(return_value="jira")), \
            patch.object(agent_run, "set_connector"), \
            patch.object(agent_run, "clear_connector"), \
            patch.object(agent_run, "build_context", AsyncMock(return_value="")):
        async with agent_run.agent_run_scope(
            agent_id="requirements", tenant_id=TENANT,
            session_id="s-1", project_id=PROJECT,
        ):
            pass
    assert factory.await_args.kwargs.get("agent_id") == "requirements"


# ── why any of it matters ────────────────────────────────────────────────────


@pytest.mark.unit
def test_an_unnamed_stage_would_have_permitted_nothing():
    """The consequence, stated once so the tests above read as more than style.

    `effective_access` returns None when agent_id is empty; None permits no mode.
    """
    assert permits(None, "read") is False
    assert permits(None, "write") is False


# ── Design's Figma tool, the same omission outside a worker ──────────────────


@pytest.mark.unit
async def test_the_figma_tool_names_the_design_stage():
    """`_figma_session` passed only `kind` and `tenant_id`. The factory documents that
    combination as permitting NOTHING, so every Figma read raised ConnectorAccessDenied
    and the Design agent's Figma integration was dead."""
    from agents_orchestrator.design_architecture_agent.tools import figma_tools

    factory = AsyncMock(return_value=object())
    with patch("config.connector_factory.get_connector_for_session", factory),             patch.object(figma_tools, "get_tenant_id", lambda: TENANT),             patch("config.ws_helper.get_project_id", lambda: PROJECT):
        connector, reason = await figma_tools._figma_session()

    assert reason == ""
    assert connector is not None
    kwargs = factory.await_args.kwargs
    assert kwargs.get("agent_id") == "design"
    assert kwargs.get("project_id") == PROJECT
    assert kwargs.get("kind") == "figma"


@pytest.mark.unit
def test_a_figma_denial_is_explained_as_an_access_level():
    """It used to fall through to the bare class name. "ConnectorAccessDenied" tells
    whoever reads it nothing about which page fixes it — and the fix is a stage access
    level, not the missing credential the other branches describe."""
    from agents_orchestrator.design_architecture_agent.tools.figma_tools import _explain
    from config.connectors.scoped import ConnectorAccessDenied

    msg = _explain(ConnectorAccessDenied("figma", "read", None))
    assert "access" in msg.lower()
    assert "integrations" in msg.lower()
    assert "ConnectorAccessDenied" not in msg


# ── the REST path: the "Pull stories" button ─────────────────────────────────


@pytest.mark.unit
async def test_the_board_picker_names_its_stage():
    """`shared.routers.projects._connector_or_409`, the seventh site with this bug.

    FOUND FROM A LIVE 502, not from reading. Clicking "Pull stories" on a fresh
    project logged:

        connector access denied: jira read (level=None)
        list_board_projects: list_projects failed: ConnectorAccessDenied
        GET /projects/{id}/board-projects 502

    `level=None` is the signature of this defect. Both callers of the helper --
    `list_board_projects` (the picker) and `ingest_board` (the actual pull) -- were
    therefore dead for EVERY project and EVERY tenant, however the board was wired.
    The 502 blamed the board ("Couldn't reach the board"), which is why it read as a
    credential problem.

    The stage is not a guess: `_board_providers` resolves the kind from
    `Project.connectors["requirements"]`, so the level must be read under that same
    stage or it names nothing.
    """
    from unittest.mock import AsyncMock, patch

    from shared.routers import projects as projects_router

    factory = AsyncMock(return_value=object())
    project = types.SimpleNamespace(
        id=PROJECT, connectors={"requirements": ["jira"]}, provider_kind=None,
    )
    with patch("config.connector_factory.get_connector_for_session", factory):
        await projects_router._connector_or_409(project, TENANT, kind="jira")

    assert factory.await_args.kwargs.get("agent_id") == "requirements"
    # And it still passes the project — the stage alone resolves nothing either.
    assert factory.await_args.kwargs.get("project_id") == str(PROJECT)
