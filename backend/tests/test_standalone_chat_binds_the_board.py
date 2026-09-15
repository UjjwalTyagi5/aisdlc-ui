"""The standalone Development and Design chats bind the stage's board for the turn.

THE LIVE FAILURE (15 Sep 2026). A developer asked the Development agent, from inside
a project whose Development stage has Azure DevOps connected read & write, whether any
epic was assigned to them. `list_ado_projects` worked (it resolves a PAT itself), but
`list_work_items` and `get_work_item` answered "Connector not available: No connector
injected — call set_connector() before invoking agent tools", and the agent asked the
user to paste Epic 116 into the chat.

The work-item tools read the connector bound on `config.connectors.context`. The
Requirements chat binds it per turn through `shared.services.agent_run.agent_run_scope`
— whose own docstring says the WS/REST chat handlers "converge on" it. The Development
and Design handlers never adopted it; they bound MCP tools only. Now they use the same
scope, so the board the project assigned to the stage, resolved for the signed-in
person, is bound for every turn — and the tools say something a user can act on when
it is not.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from agents_orchestrator.development_agent.tools import work_item_tools as wit
from config.connectors import context as cctx

ROOT = pathlib.Path(__file__).resolve().parents[1] / "agents_orchestrator"

HANDLERS = {
    "development": ROOT / "development_agent" / "development_agent_api.py",
    "design": ROOT / "design_architecture_agent" / "design_architecture_agent_api.py",
    "requirements": ROOT / "requirements_agent" / "requirements_agent_api.py",
}


@pytest.mark.parametrize("agent_id", sorted(HANDLERS))
def test_every_graph_run_in_the_handler_is_inside_agent_run_scope(agent_id):
    """Static: each place the handler runs its graph (`astream(` / `_stream_agent_response(`)
    sits inside an `agent_run_scope(agent_id=...)` block, and nothing binds MCP tools
    on its own any more (that is the scope's job, and a direct `mcp_tools_scope` is
    how the board went unbound)."""
    src = HANDLERS[agent_id].read_text(encoding="utf-8")
    assert "mcp_tools_scope(" not in src, f"{agent_id}: bind through agent_run_scope, not mcp_tools_scope"
    # The streaming helper's own body runs the graph too; its callers are what matter.
    helper = re.search(r"\nasync def _stream_agent_response\(.*?(?=\n(?:async )?def |\n@)", src, re.S)
    inside_helper = range(helper.start(), helper.end()) if helper else range(0)
    runs = [m.start() for m in re.finditer(r"\.astream\(|await _stream_agent_response\(", src)
            if m.start() not in inside_helper]
    assert runs, "the handler runs its graph somewhere"
    scopes = [m.start() for m in re.finditer(r"agent_run_scope\(\s*\n?\s*agent_id=\"" + agent_id + '"', src)]
    for pos in runs:
        # a scope opened before this run, within the same function body
        assert any(0 < pos - s < 2500 for s in scopes), (
            f"{agent_id}: graph run at offset {pos} has no agent_run_scope(agent_id={agent_id!r}) before it"
        )


def test_the_scope_is_given_the_turns_user_and_project():
    """`owner_id` and `project_id` are load-bearing (agent_run.py, orchestrator2/connectors.py):
    resolved without them the connector carries no credential."""
    for agent_id, path in HANDLERS.items():
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"agent_run_scope\((.*?)\)\s*(?:,|as)", src, re.S):
            args = m.group(1)
            assert "owner_id=" in args, (agent_id, args)
            assert "project_id=" in args, (agent_id, args)


# ── the tools, with and without a bound board ────────────────────────────────


class _Board:
    async def read_adapter(self, operation, **kwargs):
        if operation == "fetch_item_detail":
            return {"id": kwargs["item_id"], "title": "Epic 116: Order ahead", "state": "Active"}
        if operation == "list_all_items":
            return [
                {"id": 116, "title": "Core URL Shortening", "work_item_type": "Epic", "state": "New", "assigned_to": "SARTHAK KAPOOR"},
                {"id": 120, "title": "Slug validation", "work_item_type": "User Story", "state": "Active", "assigned_to": "Diego Ruiz"},
                {"id": 121, "title": "Click log table", "work_item_type": "Task", "state": "New", "assigned_to": ""},
            ]
        raise AssertionError(operation)


@pytest.fixture(autouse=True)
def _unbound():
    cctx.clear_connector()
    yield
    cctx.clear_connector()


async def test_with_the_board_bound_the_tools_answer():
    cctx.set_connector(_Board())
    out = await wit.get_work_item.ainvoke({"project": "QuickLink", "item_id": 116})
    assert "Order ahead" in out
    listed = await wit.list_work_items.ainvoke({"project": "QuickLink"})
    assert "[116]" in listed and "[120]" in listed and "[121]" in listed
    assert "SARTHAK KAPOOR" in listed and "unassigned" in listed


async def test_listing_covers_epics_and_filters_by_assignee_type_and_state():
    """"Is there an epic assigned to me?" used to get "no work items" because the
    listing was stories-only in one state. Epics are listed, and the filters combine."""
    cctx.set_connector(_Board())
    epics = await wit.list_work_items.ainvoke({"project": "QuickLink", "item_type": "epic"})
    assert "[116]" in epics and "[120]" not in epics
    mine = await wit.list_work_items.ainvoke({"project": "QuickLink", "assigned_to": "sarthak"})
    assert "[116]" in mine and "[120]" not in mine
    new_tasks = await wit.list_work_items.ainvoke({"project": "QuickLink", "state": "new", "item_type": "Task"})
    assert "[121]" in new_tasks and "[116]" not in new_tasks
    none = await wit.list_work_items.ainvoke({"project": "QuickLink", "assigned_to": "nobody-here"})
    assert none.startswith("No work items in project 'QuickLink' matching assigned to 'nobody-here'")
    assert "Epic" in none and "User Story" in none, "says what the board does hold"


async def test_without_a_board_the_tools_say_what_to_check_not_set_connector():
    for tool, args in ((wit.get_work_item, {"project": "QuickLink", "item_id": 116}),
                       (wit.list_work_items, {"project": "QuickLink"})):
        out = await tool.ainvoke(args)
        assert "set_connector" not in out
        assert "Integrations page" in out
        assert "never ask them to paste" in out
