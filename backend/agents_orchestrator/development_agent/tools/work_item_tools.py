"""Work item tools — fetch and list work items via the provider abstraction.

Works with any configured connector (ADO today, Jira/Linear when activated)
without any code changes here. Provider pattern handles connector switching.
"""
from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

from config.connectors.context import get_connector
from config.connectors.base import ConnectorNotAvailableError

logger = logging.getLogger("development_agent")

#: What the model is told when no board is bound for the turn. The bound connector
#: is the board the project assigned to the Development stage, resolved for the
#: signed-in user — so "not bound" means one of two things a user can act on.
NO_BOARD = (
    "No work-item board is connected for you on this project. Either the project "
    "has not assigned a board (Azure DevOps, Jira) to the Development stage, or your "
    "own credential for it is not saved on the project's Integrations page — board "
    "credentials are personal. Tell the user which to check; never ask them to paste "
    "the work item into the chat."
)


@tool
async def get_work_item(project: str, item_id: int) -> str:
    """Fetch a work item by ID — returns title, description, acceptance criteria, type, and state.

    Call this whenever the user gives you a work item ID so you know exactly what to build.
    Never ask the user to paste the work item content — fetch it directly.

    Args:
        project: Project name (from list_ado_projects)
        item_id: Numeric work item ID
    """
    try:
        connector = get_connector()
    except RuntimeError:
        return NO_BOARD
    try:
        detail = await connector.read_adapter("fetch_item_detail", project=project, item_id=item_id)
        return json.dumps(detail, indent=2)
    except ConnectorNotAvailableError as e:
        return str(e)
    except Exception as e:
        logger.warning("get_work_item failed: %s", e)
        return f"Error fetching work item {item_id} from '{project}': {e}"


def _matches(item: dict, *, state: str, item_type: str, assigned_to: str) -> bool:
    """Case-insensitive, substring on the assignee: "sarthak" finds "SARTHAK KAPOOR"."""
    if state and (item.get("state") or "").lower() != state.lower():
        return False
    if item_type and item_type.lower() not in (item.get("work_item_type") or "").lower():
        return False
    if assigned_to and assigned_to.lower() not in (item.get("assigned_to") or "").lower():
        return False
    return True


@tool
async def list_work_items(
    project: str, state: str = "", item_type: str = "", assigned_to: str = ""
) -> str:
    """List the work items on the board — epics, features, stories, tasks — with their
    state and assignee, so the user can pick what to build.

    Call it when the user asks what is assigned to them, what is open, or which epic
    or story to work on. Filters are optional and combine; leave them empty to see
    everything. Present the result as a numbered list and let the user pick — never
    ask them to type or paste a work item.

    THIS USED TO LIST USER STORIES ONLY, in one state, so "is there an epic assigned
    to me?" got "no work items found" while Epic 116 sat on the board in state New.

    Args:
        project:     Project name (from list_ado_projects)
        state:       e.g. "New", "Active", "In Development" — empty for any state
        item_type:   e.g. "Epic", "User Story", "Task" — empty for any type
        assigned_to: part of the assignee's name or email — empty for anyone
    """
    try:
        connector = get_connector()
    except RuntimeError:
        return NO_BOARD
    try:
        items = await connector.read_adapter("list_all_items", project=project)
    except Exception as e:
        logger.warning("list_work_items failed: %s", e)
        return f"Error listing work items in '{project}': {e}"

    wanted = [i for i in (items or []) if _matches(i, state=state, item_type=item_type, assigned_to=assigned_to)]
    filters = ", ".join(f for f in (
        f"state {state!r}" if state else "",
        f"type {item_type!r}" if item_type else "",
        f"assigned to {assigned_to!r}" if assigned_to else "",
    ) if f)
    if not wanted:
        seen = ", ".join(sorted({(i.get("work_item_type") or "?") for i in (items or [])})) or "none"
        return (
            f"No work items in project '{project}'" + (f" matching {filters}" if filters else "") + "."
            + (f" The board holds {len(items)} item(s) of type: {seen} — say if you want them listed." if items else "")
        )
    lines = [f"Work items in '{project}'" + (f" ({filters})" if filters else "") + ":\n"]
    for i, item in enumerate(wanted, 1):
        who = item.get("assigned_to") or "unassigned"
        lines.append(
            f"{i}. [{item.get('id')}] {item.get('title', 'Untitled')} "
            f"({item.get('work_item_type', 'Item')}) — {item.get('state', '')} — {who}"
        )
    lines.append("\nWhich work item do you want to implement? Reply with the ID.")
    return "\n".join(lines)
