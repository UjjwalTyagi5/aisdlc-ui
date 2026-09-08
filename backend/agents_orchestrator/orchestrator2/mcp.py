"""The project's MCP servers, loaded as tools for the duration of one turn.

THE SAME SHAPE AS `connectors.py`, and found the same way. The old engine wraps every
turn in two context managers — `_stage_connector` and `mcp_tools_for_stage` — and
`orchestrator2` had neither. That is the cost D10a recorded for reusing the compiled
graphs while skipping their `*_agent_api.py` wrappers: state the wrapper used to
provide has to be provided here instead.

WHY THIS ONE WOULD NEVER HAVE COMPLAINED. The connector gap announced itself: the
Development agent said out loud that it could not reach Azure DevOps. MCP says
nothing. The agent node binds whatever `mcp_runtime` holds, so an unbound contextvar
is simply an agent with fewer tools — no error, no warning, and an answer that looks
complete. A project that registered MCP servers would find the Orchestrator quietly
ignoring them, which is the "declared interface with no data behind it" failure this
codebase has already shipped more than once.

WHAT MAKES A SERVER RESOLVE:

  · `tenant_id`  — whose registered servers are visible.
  · `server_ids` — the project's selection FOR THIS AGENT. The map is per-agent, so
                   handing an agent another agent's servers would give it tools the
                   project never granted it.
  · `project_id` + `owner_id` — together they let a server prefer this turn's own
                   member's saved credential over the org-registered one. See
                   `mcp_registry.resolve_server_configs`.

NOTHING HERE MAY FAIL A TURN. An unreachable MCP server is somebody else's
infrastructure problem; the agent should still answer with the tools it does have.
`mcp_tools_scope` is already fail-soft internally and clears on exit; the extra guard
here covers the resolution that happens BEFORE it is entered.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from agents_orchestrator.orchestrator2.project_config import project_maps as _project_maps

logger = logging.getLogger(__name__)


async def _load_tools(
    tenant_id: str,
    server_ids: list[str],
    agent_id: str,
    project_id: str | None,
    owner_id: str,
) -> list[Any]:
    """Resolve the selected servers into LangChain tools.

    Split out so tests can replace loading without reaching into the MCP client, and
    so the argument list — every member of which is load-bearing, see the module
    docstring — is stated once.
    """
    from shared.services import mcp_client, mcp_registry

    configs = await mcp_registry.resolve_server_configs(
        tenant_id, list(server_ids), project_id=project_id, owner_id=owner_id or None,
    )
    return await mcp_client.load_tools(configs)


@asynccontextmanager
async def bound_mcp_tools(
    agent_id: str,
    *,
    tenant_id: str,
    project_id: str | None,
    owner_id: str,
) -> AsyncIterator[list]:
    """Bind this agent's MCP tools for the turn; always unbind them afterwards.

    Yields the tools that were bound — `[]` when the project selected none, which is
    the ordinary case and not a degraded one.
    """
    from shared.tools.mcp_runtime import clear_mcp_tools, set_mcp_tools

    tools: list[Any] = []
    try:
        _connectors, mcp_servers = await _project_maps(tenant_id, project_id)
        server_ids = (
            mcp_servers.get(agent_id) if isinstance(mcp_servers, dict) else None
        )
        if tenant_id and server_ids:
            tools = await _load_tools(
                tenant_id, list(server_ids), agent_id, project_id, owner_id
            )
    except Exception as exc:  # noqa: BLE001 — MCP must never fail an otherwise-fine turn
        logger.warning(
            "orchestrator2 bound no MCP tools for agent=%s project=%s: %s — the turn "
            "proceeds with the agent's own tools",
            agent_id, project_id, exc,
        )
        tools = []

    set_mcp_tools(tools)
    try:
        yield tools
    finally:
        # Unconditionally, including when nothing was bound: `set_mcp_tools([])` above
        # already wrote to the contextvar, and MCP tools can carry credentials of
        # their own. Left set they would be visible to the next turn, on another agent
        # and possibly another project.
        try:
            clear_mcp_tools()
        except Exception:  # noqa: BLE001 — nothing useful remains to do
            logger.exception("orchestrator2 could not clear the bound MCP tools")
