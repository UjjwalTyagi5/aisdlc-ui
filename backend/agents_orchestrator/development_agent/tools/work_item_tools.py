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
    except RuntimeError as e:
        return f"Connector not available: {e}"
    try:
        detail = await connector.read_adapter("fetch_item_detail", project=project, item_id=item_id)
        return json.dumps(detail, indent=2)
    except ConnectorNotAvailableError as e:
        return str(e)
    except Exception as e:
        logger.warning("get_work_item failed: %s", e)
        return f"Error fetching work item {item_id} from '{project}': {e}"


@tool
async def list_work_items(project: str, state: str = "Active") -> str:
    """List work items in a project — call to discover what needs to be built.

    Use after the user picks a project to show available items without asking
    them to type an ID manually. Present as a numbered list, let them pick.

    Args:
        project: Project name (from list_ado_projects)
        state: Work item state filter — "Active", "New", "In Development", etc.
    """
    try:
        connector = get_connector()
    except RuntimeError as e:
        return f"Connector not available: {e}"
    try:
        items = await connector.read_adapter("list_stories", project=project, state=state)
        if not items:
            return f"No '{state}' work items found in project '{project}'."
        lines = [f"Work items in '{project}' (state: {state}):\n"]
        for i, item in enumerate(items, 1):
            lines.append(
                f"{i}. [{item.get('id')}] {item.get('title', 'Untitled')} "
                f"({item.get('work_item_type', 'Item')}) — {item.get('state', '')}"
            )
        lines.append("\nWhich work item do you want to implement? Reply with the ID.")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("list_work_items failed: %s", e)
        return f"Error listing work items in '{project}': {e}"
