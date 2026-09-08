"""What the project says each of its agents should be given.

Two per-agent maps live on the `projects` row and are read together because they are
read at the same moment, by the same turn, for the same reason:

    connectors   {agent_id: [kind, ...]}        which board/repo connector to bind
    mcp_servers  {agent_id: [server_id, ...]}   which MCP servers to load as tools

Both are the state the standalone agents' `*_agent_api.py` wrappers used to supply and
that `orchestrator2` skips by design (D10a). Neither is a precondition: a project that
selects nothing is an ordinary project, so every failure here degrades to an empty map
rather than ending the turn. A socket must not die because a refinement could not be
read.

The read is tenant-scoped, and — as everywhere in this engine — that is not the only
thing scoping it: the `project_id` reaching this module came from the verified `runs`
row and never from a client frame.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def project_maps(tenant_id: str, project_id: str | None) -> tuple[dict, dict]:
    """`(connectors, mcp_servers)` for this project, or two empty maps.

    Fail-soft on every path, deliberately. The alternative — raising — would turn a
    project that simply has nothing configured, or a transient database blip, into a
    dead turn.
    """
    if not tenant_id or not project_id:
        return {}, {}
    try:
        import uuid

        from sqlalchemy import select

        from shared.db import get_db_session_for_tenant
        from shared.models.orm import Project

        async with get_db_session_for_tenant(tenant_id) as session:
            project = (
                await session.execute(
                    select(Project).where(Project.id == uuid.UUID(str(project_id)))
                )
            ).scalar_one_or_none()
            if project is None:
                return {}, {}
            return (
                getattr(project, "connectors", None) or {},
                getattr(project, "mcp_servers", None) or {},
            )
    except Exception as exc:  # noqa: BLE001 — a refinement, never fatal
        logger.warning(
            "orchestrator2 could not read the per-agent config for project=%s: %s",
            project_id, exc,
        )
        return {}, {}
