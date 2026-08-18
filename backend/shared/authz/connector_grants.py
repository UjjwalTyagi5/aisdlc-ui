"""What access does THIS project actually have to THIS connector?

The cascade in one function. `integration_grants` says what the organisation allowed
a Business Unit; `project_connector_access` says whether that unit's admin narrowed
it for one project; the answer is their intersection, computed by
`connector_access.narrow()` so the incomparable read/write pair is handled in exactly
one place.

WHY A PROJECT ROW MAY BE ABSENT. Absence means "inherit the unit's level", not "no
access". The table was added after projects were already using connectors, so reading
absence as denial would have revoked every project's integrations the moment the
migration ran. A project is narrowed only when somebody narrowed it.

FAIL CLOSED ON EVERYTHING ELSE. No grant, unknown project, missing tenant, a
database that will not answer — all return None, which every caller must treat as no
access. The one thing this must never do is return a level it is not sure about,
because the caller is about to hand an agent a live connector.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.connector_access import AccessLevel, narrow

logger = logging.getLogger(__name__)


async def unit_access(
    db: AsyncSession,
    *,
    tenant_id: str,
    workspace_id: str,
    target_ref: str,
    kind: str = "connector",
) -> Optional[AccessLevel]:
    """The level the organisation granted this unit, or None if it granted nothing."""
    if not tenant_id or not workspace_id or not target_ref:
        return None
    row = (
        await db.execute(
            text(
                "SELECT access FROM integration_grants "
                "WHERE tenant_id = CAST(:t AS uuid) AND workspace_id = CAST(:w AS uuid) "
                "  AND kind = :k AND target_ref = :r"
            ),
            {"t": tenant_id, "w": workspace_id, "k": kind, "r": target_ref},
        )
    ).first()
    return row.access if row is not None else None


async def project_override(
    db: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    target_ref: str,
    kind: str = "connector",
) -> Optional[AccessLevel]:
    """The narrowing a unit admin set for this project, or None meaning 'inherit'."""
    if not tenant_id or not project_id or not target_ref:
        return None
    row = (
        await db.execute(
            text(
                "SELECT access FROM project_connector_access "
                "WHERE tenant_id = CAST(:t AS uuid) AND project_id = CAST(:p AS uuid) "
                "  AND kind = :k AND target_ref = :r"
            ),
            {"t": tenant_id, "p": project_id, "k": kind, "r": target_ref},
        )
    ).first()
    return row.access if row is not None else None


async def effective_access(
    db: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    target_ref: str,
    kind: str = "connector",
) -> Optional[AccessLevel]:
    """The access this project really has: unit grant ∩ project narrowing.

    Returns None for "none at all", which is the answer for an ungranted connector,
    an unknown project, and a narrowing that shares no operation with its grant.
    """
    if not tenant_id or not project_id:
        return None

    workspace = (
        await db.execute(
            text("SELECT workspace_id FROM projects WHERE id = CAST(:p AS uuid)"),
            {"p": project_id},
        )
    ).scalar()
    if workspace is None:
        return None

    granted = await unit_access(
        db, tenant_id=tenant_id, workspace_id=str(workspace), target_ref=target_ref, kind=kind
    )
    if granted is None:
        return None

    override = await project_override(
        db, tenant_id=tenant_id, project_id=project_id, target_ref=target_ref, kind=kind
    )
    if override is None:
        # Not narrowed — the project inherits its unit's level whole.
        return granted
    return narrow(granted, override)


async def resolve_effective_access(
    tenant_id: str, project_id: str, target_ref: str, kind: str = "connector"
) -> Optional[AccessLevel]:
    """`effective_access` for callers with no session — the agent runtime.

    Opens its own tenant-scoped session, because `integration_grants` and
    `project_connector_access` are both FORCE RLS and read nothing without the GUC.
    Returns None on any failure: a run that cannot prove its access does not get it.
    """
    if not tenant_id or not project_id:
        return None
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415 — import cycle

    try:
        async with get_db_session_for_tenant(tenant_id) as session:
            return await effective_access(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                target_ref=target_ref,
                kind=kind,
            )
    except Exception:  # noqa: BLE001 — fail closed, never fail open
        logger.exception(
            "connector access resolution failed (tenant=%s project=%s target=%s)",
            tenant_id, project_id, target_ref,
        )
        return None
