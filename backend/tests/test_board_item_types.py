"""Work item types are per project, and the agent must not guess them.

From a live run against a real Azure DevOps project on the BASIC process:

    the board rejected it (HTTP 404) — VS402323: Work item type User Story does not
    exist in project efd98e99-... or you do not have permission to access it.

`create_board_item` defaults to "User Story", which exists on the Agile template and
not on Basic (Epic/Issue/Task) or Scrum (Product Backlog Item). A static alias map —
the fix used for Jira, where the vocabulary difference is fixed — is WRONG here,
because the correct answer depends on the project's process template. So the agent gets
a way to ask instead.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _Board:
    display_name = "Azure Boards"
    connector_name = "azure_devops"

    def __init__(self, types=None, raises=None):
        self._types = types or []
        self._raises = raises
        self.calls = []

    async def read_adapter(self, op, **kw):
        self.calls.append((op, kw))
        if self._raises:
            raise self._raises
        return self._types


def _with(board):
    from agents_orchestrator.requirements_agent.agents import planning

    return patch.object(planning, "_board_connector", AsyncMock(return_value=(board, None)))


# ── the tool ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_it_lists_the_types_the_project_really_has():
    from agents_orchestrator.requirements_agent.agents import planning

    board = _Board([
        {"name": "Epic", "description": "Large body of work"},
        {"name": "Issue", "description": "A basic unit of work"},
        {"name": "Task", "description": ""},
    ])
    with _with(board):
        out = await planning.list_board_item_types.ainvoke({"project": "sdlc"})

    assert "Epic" in out and "Issue" in out and "Task" in out
    # A Basic-process project genuinely has no User Story, and the answer must not
    # invent one to match what the agent expected.
    assert "User Story" not in out
    assert board.calls[0][0] == "list_item_types"
    assert board.calls[0][1]["project"] == "sdlc"


@pytest.mark.unit
async def test_it_tells_the_model_to_use_the_exact_name():
    """Without this the model reads the list and still sends its own phrasing."""
    from agents_orchestrator.requirements_agent.agents import planning

    with _with(_Board([{"name": "Issue"}])):
        out = await planning.list_board_item_types.ainvoke({"project": "sdlc"})
    assert "EXACT" in out


@pytest.mark.unit
async def test_a_board_failure_does_not_leak_the_instance_url():
    import httpx

    from agents_orchestrator.requirements_agent.agents import planning

    request = httpx.Request("GET", "https://dev.azure.com/acme/_apis/wit/workitemtypes")
    response = httpx.Response(403, json={"message": "TF400813: not authorised"}, request=request)
    board = _Board(raises=httpx.HTTPStatusError("no", request=request, response=response))
    with _with(board):
        out = await planning.list_board_item_types.ainvoke({"project": "sdlc"})
    assert "dev.azure.com" not in out
    assert "TF400813" in out


@pytest.mark.unit
async def test_an_empty_answer_is_reported_plainly():
    from agents_orchestrator.requirements_agent.agents import planning

    with _with(_Board([])):
        out = await planning.list_board_item_types.ainvoke({"project": "sdlc"})
    assert "No work item types" in out


@pytest.mark.unit
async def test_it_takes_a_provider_like_every_other_board_tool():
    from agents_orchestrator.requirements_agent.agents import planning

    props = planning.list_board_item_types.args_schema.model_json_schema()["properties"]
    assert "provider" in props


@pytest.mark.unit
def test_the_tool_is_registered():
    from agents_orchestrator.requirements_agent.agents import planning

    assert "list_board_item_types" in {t.name for t in planning._BOARD_TOOLS}


# ── both connectors can answer it ────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("module", "cls"),
    [
        ("config.connectors.azure_devops", "AzureDevOpsConnector"),
        ("config.connectors.jira", "JiraConnector"),
    ],
)
def test_both_board_connectors_implement_it(module, cls):
    """A tool that only one provider can answer is a tool that fails for half the
    tenants — and this one is reached precisely when something has already gone wrong."""
    import importlib

    connector = getattr(importlib.import_module(module), cls)
    assert hasattr(connector, "list_item_types")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("module", "cls"),
    [
        ("config.connectors.azure_devops", "AzureDevOpsConnector"),
        ("config.connectors.jira", "JiraConnector"),
    ],
)
def test_both_declare_the_capability(module, cls):
    """read_adapter dispatches on the operation name; the manifest is what tells the
    rest of the platform it exists."""
    import importlib
    import inspect

    src = inspect.getsource(getattr(importlib.import_module(module), cls))
    assert '"list_item_types": self.list_item_types' in src
    assert '"list_item_types": CapabilityEntry' in src


# ── the prompt stops the agent promising what it has no tool for ─────────────


@pytest.mark.unit
def test_the_prompt_forbids_claiming_a_parent_link():
    """It reported "created the 3 Tasks and linked them under Epic #1". create_work_item
    sends no relations at all, so the link never existed — the items are unparented and
    the only trace of the Epic is a sentence in a description."""
    from agents_orchestrator.requirements_agent.agents.planning import INGESTION_SYS_MESSAGE

    assert "CANNOT link a work item to a parent" in INGESTION_SYS_MESSAGE
    assert "unparented" in INGESTION_SYS_MESSAGE.lower()


@pytest.mark.unit
@pytest.mark.parametrize(
    "capability",
    ["area path", "iteration", "board column", "saved quer", "assign a work item"],
)
def test_the_prompt_names_the_things_it_cannot_do(capability):
    """It offered to configure board columns, create an area path and an iteration, and
    add a saved query. There is no tool for any of them."""
    from agents_orchestrator.requirements_agent.agents.planning import INGESTION_SYS_MESSAGE

    assert capability in INGESTION_SYS_MESSAGE.lower()


@pytest.mark.unit
def test_the_prompt_forbids_reporting_work_that_did_not_happen():
    from agents_orchestrator.requirements_agent.agents.planning import INGESTION_SYS_MESSAGE

    assert "NEVER REPORT WORK YOU DID NOT DO" in INGESTION_SYS_MESSAGE


@pytest.mark.unit
def test_the_prompt_points_at_the_discovery_tool_by_name():
    from agents_orchestrator.requirements_agent.agents.planning import INGESTION_SYS_MESSAGE

    assert "list_board_item_types" in INGESTION_SYS_MESSAGE
    assert "VS402323" in INGESTION_SYS_MESSAGE
