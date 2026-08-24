"""What access does THIS STAGE of THIS project actually have to THIS connector?

Three questions in order, and the first `no` ends it:

  1. MAY THE UNIT REACH IT AT ALL?  `integration_grants` — the Org Admin's decision,
     and the only one left above the project. It is a reach decision, not a level:
     a row means "this Business Unit may use Jira", full stop. Revoking it stops
     every stage of every project in that unit at once.
  2. IS IT WIRED TO THIS STAGE?     `projects.connectors[agent_id]` — a connector
     the project never assigned to this stage is not available to it, which makes
     the stage assignment a real boundary rather than decoration.
  3. AT WHAT LEVEL?                 `projects.tool_access_modes[stage::kind::ref]` —
     read, write, or both, chosen per stage so Jira can be read-only for QA and
     read-write for Development.

WHERE THE CEILING WENT (migration 0024). `integration_grants.access` used to bound
step 3 from above, and the two were intersected. That column is gone: the level is
decided at step 3 and nothing overrules it. This is a deliberate trade — see the
migration's docstring. The intersection helpers that served it (`narrow`,
`contains`) were deleted from `connector_access` rather than left unused, so there
is no half-live ceiling machinery to mistake for the current rule.

Do not reintroduce a level on the grant without deciding which of the two wins:
two levels that disagree is what this replaced.

WHY AN UNSET MODE IS read_write. The picker documents an untouched chip as "both" and
has always shown it that way, so a project that assigned a connector to a stage and
never opened the chip means "both". With the ceiling gone that default is the final
answer rather than something a grant could still narrow, which makes it worth stating
loudly: ASSIGNING A CONNECTOR TO A STAGE GRANTS READ AND WRITE UNLESS SOMEBODY SAYS
OTHERWISE. Step 2 is what keeps that from being a blanket grant.

FAIL CLOSED ON EVERYTHING ELSE. No grant, unknown project, unknown stage, missing
tenant, a mode string nobody recognises, a database that will not answer — all return
None, which every caller must treat as no access. The one thing this must never do is
return a level it is not sure about, because the caller is about to hand an agent a
live connector.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.connector_access import (
    AccessLevel,
    DEFAULT_TOOL_MODE,
    level_from_mode,
    stage_mode_key,
)

logger = logging.getLogger(__name__)


async def unit_is_granted(
    db: AsyncSession,
    *,
    tenant_id: str,
    workspace_id: str,
    target_ref: str,
    kind: str = "connector",
) -> bool:
    """Did the organisation give this unit reach to this integration at all?

    A boolean since migration 0024. It used to return the granted LEVEL, which was
    then a ceiling on the project — the level now lives on the project's stage and
    the grant answers only whether the unit may touch the integration.
    """
    if not tenant_id or not workspace_id or not target_ref:
        return False
    try:
        _uuid.UUID(workspace_id)
    except ValueError:
        # See granted_target_refs's identical guard — a malformed id fails
        # closed here rather than raising past the caller.
        return False
    row = (
        await db.execute(
            text(
                "SELECT 1 FROM integration_grants "
                "WHERE tenant_id = CAST(:t AS uuid) AND workspace_id = CAST(:w AS uuid) "
                "  AND kind = :k AND target_ref = :r"
            ),
            {"t": tenant_id, "w": workspace_id, "k": kind, "r": target_ref},
        )
    ).first()
    return row is not None


async def granted_target_refs(
    db: AsyncSession,
    *,
    tenant_id: str,
    workspace_id: str,
    kind: str = "connector",
) -> set[str]:
    """Every target_ref this unit was granted, in one query.

    Same answer as calling `unit_is_granted` once per candidate, but a picker
    checking a whole catalogue (see GET /connectors) would otherwise issue one
    round trip per kind — noticeable once the catalogue is a dozen-plus entries.
    """
    if not tenant_id or not workspace_id:
        return set()
    try:
        _uuid.UUID(workspace_id)
    except ValueError:
        # A malformed id (stale cookie, mock-mode leftover, tampered header) is
        # "no unit to check against" — same answer as no workspace at all, not a
        # 500. asyncpg validates UUIDs client-side and raises before the query
        # ever reaches Postgres, so this has to be caught here rather than left
        # to fail closed at the database.
        return set()
    rows = (
        await db.execute(
            text(
                "SELECT target_ref FROM integration_grants "
                "WHERE tenant_id = CAST(:t AS uuid) AND workspace_id = CAST(:w AS uuid) "
                "  AND kind = :k"
            ),
            {"t": tenant_id, "w": workspace_id, "k": kind},
        )
    ).scalars().all()
    return set(rows)


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
    agent_id: str = "",
) -> Optional[AccessLevel]:
    """The access this project's STAGE really has. See the module docstring.

    `agent_id` names the stage asking. It is required in practice — omitting it is
    the caller saying "no stage", and a request that belongs to no stage cannot be
    checked against one, so it yields None rather than a project-wide answer. The
    parameter keeps a default only so the signature stays keyword-compatible with the
    handful of read-only report callers that pass every other field by name.

    Returns None for "none at all": an ungranted unit, an unknown project, a
    connector this stage never wired, or a mode nobody recognises.
    """
    if not tenant_id or not project_id:
        return None

    row = (
        await db.execute(
            text(
                "SELECT workspace_id, connectors, mcp_servers, tool_access_modes "
                "FROM projects WHERE id = CAST(:p AS uuid)"
            ),
            {"p": project_id},
        )
    ).first()
    if row is None or row.workspace_id is None:
        return None

    # 1. Reach. The Org Admin's only remaining say, and still a hard stop.
    if not await unit_is_granted(
        db, tenant_id=tenant_id, workspace_id=str(row.workspace_id),
        target_ref=target_ref, kind=kind,
    ):
        return None

    if not agent_id:
        return None

    # 2. Wiring. `connectors` / `mcp_servers` are {agent_id: [target_ref, ...]}, the
    #    same maps the stage picker writes. A stage that never wired this tool has no
    #    access to it however the unit was granted — with the ceiling gone this is the
    #    boundary that stops one grant reaching every agent.
    assigned = (row.connectors if kind == "connector" else row.mcp_servers) or {}
    if target_ref not in (assigned.get(agent_id) or []):
        return None

    # 3. Level, most specific first:
    #      the stage's own mode          projects.tool_access_modes
    #      the project-wide default      project_connector_access
    #      the picker's documented default ("both")
    #
    #    The middle step is what keeps `project_connector_access` meaningful after the
    #    ceiling was removed. It was the project's narrowing UNDER a unit level; it is
    #    now the project's default OVER its stages. Deleting it would have silently
    #    widened every project that had narrowed itself, and leaving it unread would
    #    have left a settings page whose control does nothing.
    modes = row.tool_access_modes or {}
    mode = modes.get(stage_mode_key(agent_id, kind, target_ref))
    if mode is not None:
        return level_from_mode(mode)

    project_default = await project_override(
        db, tenant_id=tenant_id, project_id=project_id, target_ref=target_ref, kind=kind
    )
    if project_default is not None:
        return project_default

    return level_from_mode(DEFAULT_TOOL_MODE)


async def resolve_effective_access(
    tenant_id: str,
    project_id: str,
    target_ref: str,
    kind: str = "connector",
    agent_id: str = "",
) -> Optional[AccessLevel]:
    """`effective_access` for callers with no session — the agent runtime.

    Opens its own tenant-scoped session, because `integration_grants` and `projects`
    are both FORCE RLS and read nothing without the GUC. Returns None on any failure:
    a run that cannot prove its access does not get it.

    `agent_id` is the stage the run is executing. A caller that cannot name one gets
    None — see `effective_access`.
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
                agent_id=agent_id,
                kind=kind,
            )
    except Exception:  # noqa: BLE001 — fail closed, never fail open
        logger.exception(
            "connector access resolution failed (tenant=%s project=%s target=%s)",
            tenant_id, project_id, target_ref,
        )
        return None
