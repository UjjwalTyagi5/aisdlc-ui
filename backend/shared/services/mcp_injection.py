"""Shared MCP-tool injection for agent graph runs.

Single source of truth for "resolve the MCP servers selected for this stage,
turn them into LangChain tools, and make them visible to the graph for the
duration of one invocation". Used by:

  - the Temporal activities (pipeline runs) — server ids come from
    SDLCWorkflowInput.mcp_servers (see workflows/activities/_base.py)
  - the interactive chat handlers (WS/REST) — server ids come from the
    project's per-stage map (see shared/services/agent_run.py)

Both converge here so the resolution/injection semantics are identical across
surfaces. MCP must never break an agent turn: any resolution/connection error
degrades to "no MCP tools" (logged), never raised.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from config.env import MCP_ENABLED
from shared.tools.mcp_runtime import clear_mcp_tools, set_mcp_tools

logger = logging.getLogger(__name__)


@asynccontextmanager
async def mcp_tools_scope(
    tenant_id: Optional[str], server_ids: Optional[list[str]], agent_id: str
) -> AsyncIterator[list]:
    """Bind the given MCP servers' tools on the mcp_runtime contextvar for the
    block, and clear on exit. No-op (empty tool set) when MCP is disabled, no
    tenant/servers, or on any resolution error.

    The contextvar set here is read by the agent node (binds base + MCP tools)
    and the dynamic tool node (dispatch), both of which run within this block.
    """
    tools: list = []
    if MCP_ENABLED and tenant_id and server_ids:
        try:
            from shared.services import mcp_client, mcp_registry  # noqa: PLC0415

            configs = await mcp_registry.resolve_server_configs(tenant_id, list(server_ids))
            tools = await mcp_client.load_tools(configs)
        except Exception as exc:  # noqa: BLE001 — never fail the turn on MCP errors
            logger.warning("MCP tool injection skipped for %s: %s", agent_id, exc)
            tools = []
    set_mcp_tools(tools)
    try:
        yield tools
    finally:
        clear_mcp_tools()


async def project_stage_server_ids(
    tenant_id: Optional[str], project_id: Optional[str], agent_id: str
) -> list[str]:
    """Return the MCP server ids the project assigned to this stage.

    Reads Project.mcp_servers[agent_id] (the per-stage map chosen at project
    creation / in Settings → Tools per stage). Returns [] when MCP is disabled,
    ids are missing, or the lookup fails — the interactive chat then simply runs
    with no MCP tools, matching a project that assigned none.
    """
    if not (MCP_ENABLED and tenant_id and project_id):
        return []
    try:
        from sqlalchemy import select  # noqa: F401,PLC0415  (kept for parity; get() used below)
        from shared.db import get_db_session_for_tenant  # noqa: PLC0415
        from shared.models.orm import Project  # noqa: PLC0415

        async with get_db_session_for_tenant(tenant_id) as session:
            project = await session.get(Project, _uuid.UUID(str(project_id)))
            mapping = (getattr(project, "mcp_servers", None) if project else None) or {}
            ids = mapping.get(agent_id) or []
            return [str(i) for i in ids]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "MCP project-map lookup failed (project=%s agent=%s): %s",
            project_id, agent_id, type(exc).__name__,
        )
        return []
