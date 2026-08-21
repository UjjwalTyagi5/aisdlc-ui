"""The project-wide DEFAULT access for an integration its unit was granted.

WHAT THIS BECAME (migration 0024). It used to be the third rung of a cascade: the
unit grant carried a level, and this narrowed it for one project. The grant no longer
carries a level — the read/write decision moved down to the individual stage, in
`projects.tool_access_modes` — so this row is no longer a narrowing under anything.

It is now the project's DEFAULT: the level a stage lands on when its own chip was
never set. `shared/authz/connector_grants.effective_access` reads the two in that
order, most specific first. That gives a project one place to say "Jira is read-only
here" without visiting all eight stages, while a stage that disagrees still wins.

WHO MAY SET IT. A Business Unit Admin, because deciding what each of their projects
may do is what running a unit means; and a Project Admin, because their own project
is theirs to configure. `assert_can_administer_project` encodes exactly that pair, so
it is reused rather than restated.

NOTHING BOUNDS IT FROM ABOVE ANY MORE, and that is a deliberate trade rather than an
oversight — see the migration. The unit grant is now a reach decision only: it can
stop a project using an integration at all, but not say what it may do with it.

ABSENCE MEANS "the picker default" (both), NOT DENY. Deleting the row is how you undo
a project-wide default, and it is a real operation rather than a synonym for
revoking — revoking is the unit's grant going away, which happens a rung up.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.connector_access import (
    ACCESS_LEVELS,
    DEFAULT_TOOL_MODE,
    is_access_level,
    label,
    level_from_mode,
)
from shared.authz.connector_capabilities import unsupported_reason, warnings_for
from shared.authz.connector_grants import unit_is_granted
from shared.authz.dependency import require_permission
from shared.authz.project_scope import assert_can_administer_project, resolve_project
from shared.db import get_db_session

logger = logging.getLogger(__name__)

project_connector_access_router = APIRouter(
    # The read-only floor. Which access a project holds is not a secret from the
    # people working in it — and the write routes below carry their own, stricter
    # check, because seeing the level and setting it are different acts.
    dependencies=[Depends(require_permission("artifact:view"))],
)


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


async def _project_or_404(db: AsyncSession, tenant_id: str, project_id: str) -> Any:
    project = await resolve_project(db, tenant_id, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="not found")
    return project


class ProjectAccessIn(BaseModel):
    # 'connector' | 'mcp', matching integration_grants.kind.
    kind: str = "connector"
    targetId: str
    access: str


@project_connector_access_router.get("/projects/{project_id}/integrations/access")
async def list_project_integration_access(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Every integration this project's unit was granted, and what the project gets.

    Reports the project-wide DEFAULT alongside the effective level. It no longer
    reports a unit level, because the unit no longer has one (migration 0024) — a
    grant is reach only. `effectiveAccess` here is the project-wide answer: what a
    stage gets when its own chip is unset. A stage that set one overrides it, and
    the stage picker is where that is seen.
    """
    tenant_id = _tenant_id(request)
    project = await _project_or_404(db, tenant_id, project_id)

    granted = (
        await db.execute(
            text(
                "SELECT kind, target_ref FROM integration_grants "
                "WHERE tenant_id = CAST(:t AS uuid) AND workspace_id = :w "
                "ORDER BY kind, target_ref"
            ),
            {"t": tenant_id, "w": project.workspace_id},
        )
    ).fetchall()

    overrides = {
        (r.kind, r.target_ref): r.access
        for r in (
            await db.execute(
                text(
                    "SELECT kind, target_ref, access FROM project_connector_access "
                    "WHERE tenant_id = CAST(:t AS uuid) AND project_id = :p"
                ),
                {"t": tenant_id, "p": project.id},
            )
        ).fetchall()
    }

    out: list[dict[str, Any]] = []
    for row in granted:
        override = overrides.get((row.kind, row.target_ref))
        # The project-wide answer, which is what this page is about. `effective_access`
        # needs a stage to give a stage's answer, and there is no stage here — so the
        # default chain is applied directly rather than calling it with a blank one.
        effective = override or level_from_mode(DEFAULT_TOOL_MODE)
        out.append(
            {
                "kind": row.kind,
                "targetId": row.target_ref,
                # None means "no project-wide default" — stages fall through to "both".
                "projectAccess": override,
                "effectiveAccess": effective,
                "effectiveLabel": label(effective),
                "inherited": override is None,
            }
        )
    return out


@project_connector_access_router.put("/projects/{project_id}/integrations/access")
async def set_project_integration_access(
    project_id: str,
    body: ProjectAccessIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Narrow one integration for this project. Never widen it past the unit's grant."""
    tenant_id = _tenant_id(request)
    project = await _project_or_404(db, tenant_id, project_id)
    await assert_can_administer_project(db, request, project)

    if body.kind not in ("connector", "mcp"):
        raise HTTPException(status_code=422, detail="kind must be 'connector' or 'mcp'")
    if not is_access_level(body.access):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "bad_access_level",
                "message": f"access must be one of {', '.join(ACCESS_LEVELS)}.",
            },
        )

    granted = await unit_is_granted(
        db,
        tenant_id=tenant_id,
        workspace_id=str(project.workspace_id),
        target_ref=body.targetId,
        kind=body.kind,
    )
    if not granted:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "not_granted",
                "message": (
                    "This business unit has not been given that integration. Ask an "
                    "Organization Admin to grant it first."
                ),
            },
        )

    # Same manifest check as the unit door — a project cannot be narrowed TO a mode
    # the connector does not implement either.
    #
    # AFTER the grant check, deliberately. A caller whose unit holds no grant should
    # be told that and nothing else: "slack has no read capabilities" answers a
    # question they were not able to ask, and confirms a connector kind exists to
    # somebody with no access to it.
    if body.kind == "connector":
        reason = unsupported_reason(body.targetId, body.access)
        if reason:
            raise HTTPException(
                status_code=422,
                detail={"code": "unsupported_access_level", "message": reason},
            )

    # THE ESCALATION REFUSAL IS GONE, and its absence is the point of migration 0024.
    # There used to be a check here that the requested level fitted inside the unit's
    # granted level. The grant no longer carries a level, so there is nothing above
    # this to exceed: whoever may administer the project decides read/write, and the
    # only thing an Org Admin can still do is revoke the integration outright.
    #
    # If a ceiling is ever wanted again, it belongs back on the grant — do not
    # reintroduce it here, where it would bound the project-wide default while
    # leaving per-stage modes (the actual decision) unbounded.

    await db.execute(
        text(
            "INSERT INTO project_connector_access "
            "  (tenant_id, project_id, kind, target_ref, access, granted_by) "
            "VALUES (CAST(:t AS uuid), :p, :k, :r, :a, :by) "
            "ON CONFLICT (tenant_id, project_id, kind, target_ref) DO UPDATE "
            "  SET access = EXCLUDED.access, granted_by = EXCLUDED.granted_by"
        ),
        {
            "t": tenant_id, "p": project.id, "k": body.kind, "r": body.targetId,
            "a": body.access, "by": getattr(request.state, "user_id", None),
        },
    )
    await db.flush()
    logger.info(
        "project integration access set: project=%s %s %s -> %s",
        project.id, body.kind, body.targetId, body.access,
    )
    return {
        "ok": True,
        "kind": body.kind,
        "targetId": body.targetId,
        "projectAccess": body.access,
        "effectiveAccess": body.access,
        "warnings": warnings_for(body.targetId, body.access) if body.kind == "connector" else [],
    }


@project_connector_access_router.delete("/projects/{project_id}/integrations/access")
async def clear_project_integration_access(
    project_id: str,
    request: Request,
    kind: str = "connector",
    targetId: str = "",
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Clear the project-wide default — its stages fall through to "both".

    NOT a revoke. Revoking is the unit's grant going away, a rung up in
    `integration_access.py`; this only removes the project's own opinion. Deleting a
    row that does not exist is a no-op rather than a 404: the caller's intent is
    "this project should hold no default", and it already does.
    """
    tenant_id = _tenant_id(request)
    project = await _project_or_404(db, tenant_id, project_id)
    await assert_can_administer_project(db, request, project)

    if not targetId:
        raise HTTPException(status_code=422, detail="targetId is required")

    result = await db.execute(
        text(
            "DELETE FROM project_connector_access "
            "WHERE tenant_id = CAST(:t AS uuid) AND project_id = :p "
            "  AND kind = :k AND target_ref = :r"
        ),
        {"t": tenant_id, "p": project.id, "k": kind, "r": targetId},
    )
    await db.flush()

    # With the row gone the project-wide answer is the picker's default; a stage that
    # set its own chip still overrides it, which is why this is not read back from
    # `effective_access` (that needs a stage, and this endpoint has none).
    return {
        "ok": True,
        "cleared": bool(result.rowcount),
        "effectiveAccess": level_from_mode(DEFAULT_TOOL_MODE),
    }
