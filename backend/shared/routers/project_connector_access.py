"""What access does ONE project have to an integration its unit was granted?

THE THIRD RUNG OF THE CASCADE. `integration_access.py` owns the middle one — whether
a Business Unit may use a thing at all, which is the Organization Admin's decision.
This owns the one below it: whether a particular project gets the whole of that
grant or a narrower slice of it.

WHO MAY SET IT, AND WHY BOTH. A Business Unit Admin sets it because deciding what
each of their projects may do is what running a unit means. A Project Admin sets it
because tightening their own project needs no permission from above — you may always
ask for less. `assert_can_administer_project` already encodes exactly that pair, so
it is reused rather than restated.

NEITHER MAY EXCEED THE UNIT'S GRANT, and that is the whole point of the rung. The
check is `contains()` and the refusal is explicit: a request for more than the unit
holds is REFUSED rather than silently narrowed, because somebody who asked for write
and quietly received read would go on believing they had write. `narrow()` computes
what a level yields; `contains()` decides whether to accept it at all. Both exist and
they answer different questions.

ABSENCE MEANS INHERIT, NOT DENY. A project with no row here gets its unit's level
whole. Deleting the row is therefore how you undo a narrowing, and it is a real
operation rather than a synonym for revoking — revoking is the unit's grant going
away, which happens a rung up.
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
    contains,
    is_access_level,
    label,
)
from shared.authz.connector_grants import effective_access, unit_access
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

    Reports the unit's level ALONGSIDE the effective one, because the two together
    are what a reader needs: "read, and your unit has read_write" is a different
    situation from "read, and that is all there is" — the first is a narrowing
    somebody chose and can undo here, the second needs a decision a rung up.
    """
    tenant_id = _tenant_id(request)
    project = await _project_or_404(db, tenant_id, project_id)

    granted = (
        await db.execute(
            text(
                "SELECT kind, target_ref, access FROM integration_grants "
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
        effective = await effective_access(
            db,
            tenant_id=tenant_id,
            project_id=str(project.id),
            target_ref=row.target_ref,
            kind=row.kind,
        )
        out.append(
            {
                "kind": row.kind,
                "targetId": row.target_ref,
                "unitAccess": row.access,
                # None means "not narrowed" — the project inherits.
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

    granted = await unit_access(
        db,
        tenant_id=tenant_id,
        workspace_id=str(project.workspace_id),
        target_ref=body.targetId,
        kind=body.kind,
    )
    if granted is None:
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

    # THE ESCALATION REFUSAL. Refused, not narrowed: somebody who asked for write and
    # quietly received read would go on believing they had write, and the first sign
    # otherwise would be an agent failing mid-run.
    if not contains(granted, body.access):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "exceeds_grant",
                "message": (
                    f"This business unit has {label(granted)} access to that "
                    f"integration, so a project cannot be given {label(body.access)}. "
                    "Ask an Organization Admin to widen the unit's grant first."
                ),
            },
        )

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
        "unitAccess": granted,
        "projectAccess": body.access,
        "effectiveAccess": body.access,
    }


@project_connector_access_router.delete("/projects/{project_id}/integrations/access")
async def clear_project_integration_access(
    project_id: str,
    request: Request,
    kind: str = "connector",
    targetId: str = "",
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Undo a narrowing — the project goes back to inheriting its unit's level.

    NOT a revoke. Revoking is the unit's grant going away, a rung up in
    `integration_access.py`; this only removes the project's own narrower opinion.
    Deleting a row that does not exist is a no-op rather than a 404: the caller's
    intent is "this project should inherit", and it already does.
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

    inherited = await effective_access(
        db, tenant_id=tenant_id, project_id=str(project.id), target_ref=targetId, kind=kind
    )
    return {
        "ok": True,
        "cleared": bool(result.rowcount),
        "effectiveAccess": inherited,
    }
