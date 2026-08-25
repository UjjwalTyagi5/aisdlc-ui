"""WHICH project — the scope check the per-project routers were missing.

`require_permission` answers "may this caller do this verb *somewhere* in the tenant".
For a route shaped `/dev/{project_id}/workspace/tree` that is the wrong question, and
five routers were asking only it: `dev_workspace`, `code_review_workspace`,
`security_workspace`, `deployment_workspace` and `documentation_workspace` took
`{project_id}` straight from the path and filtered on `tenant_id` alone. Their only gate
was the `artifact:view` floor applied at include time — a permission `contributor` holds,
and `contributor` is the role whose entire designed purpose is to hold nothing until a
unit admin assigns a real one. So the least-privileged account in the system could read
any project's source tree and trigger clones, scans and deploy-prepares against it.

`runs.py` had already solved this with its `_get_run_or_404` chokepoint. This module is
the same idea for projects, expressed as a ROUTER-LEVEL DEPENDENCY rather than a call in
each handler: the five routers carry 24 routes between them, none of which takes a `db`
session, and a per-handler check would have meant 24 signature changes plus the standing
risk that route 25 forgets. `APIRouter(dependencies=[Depends(require_project_access())])`
covers every current route and every future one.

TWO QUESTIONS, NOT ONE. Reading a project and administering it are different, and both
are needed:
  · `require_project_access` — may you SEE this project? A developer bound to it must be
    able to pull its repo, so this is the readable set.
  · `assert_can_administer_project` — do you RUN it? Editing its budget or settings is
    the parent unit's admin or the project's own admin, never every member.

See finding 3 in `docs/rbac-audit-2026-08-17.md`.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.can_perform import visible_project_ids
from shared.authz.read_scope import (
    administered_workspace_ids,
    is_org_wide,
    live_binding,
)
from shared.db import get_db_session
from shared.routers._schemas import _slugify

logger = logging.getLogger(__name__)


def _is_uuid(value: str) -> bool:
    try:
        _uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


async def resolve_project(
    db: AsyncSession, tenant_id: str, project_id: str
) -> Any | None:
    """Find a project by UUID **or by derived slug**, within the tenant. None if absent.

    The slug branch is not optional: `slug` is a derived display field with no column,
    and the frontend's project routes are slug-based (`/dev/payments-portal/...`), so a
    UUID-only lookup would 404 every real request. Mirrors `projects.py::_get_or_404`,
    which is where this resolution rule already lived.
    """
    if not project_id:
        return None
    if _is_uuid(project_id):
        return (
            await db.execute(
                text(
                    "SELECT id, workspace_id, display_name FROM projects "
                    "WHERE id = CAST(:p AS uuid) AND tenant_id = CAST(:t AS uuid)"
                ),
                {"p": project_id, "t": tenant_id},
            )
        ).first()

    rows = (
        await db.execute(
            text(
                "SELECT id, workspace_id, display_name FROM projects "
                "WHERE tenant_id = CAST(:t AS uuid)"
            ),
            {"t": tenant_id},
        )
    ).fetchall()
    return next((r for r in rows if _slugify(r.display_name) == project_id), None)


async def assert_can_read_project(
    db: AsyncSession, request: Request, project_id: str
) -> Any:
    """Refuse a request aimed at a project the caller cannot see. Returns the project.

    404 rather than 403 throughout, matching `assert_can_write_workspace` and
    `_assert_can_write_project`: a project the caller cannot reach must not be confirmed
    to exist by the error code. "Not found" and "not yours" are deliberately the same
    answer.
    """
    tenant_id = getattr(request.state, "tenant_id", "") or ""
    if not tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    project = await resolve_project(db, tenant_id, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="not found")

    if is_org_wide(request):
        return project

    user_id = getattr(request.state, "user_id", "") or ""
    visible = await visible_project_ids(db, user_id=user_id, tenant_id=tenant_id)
    # None means "every project in the tenant"; [] means "none". Conflating them is the
    # mistake read_scope.py warns about — treating [] as "no filter" would show a
    # brand-new account everything.
    if visible is None:
        return project
    if str(project.id) not in visible:
        logger.info(
            "project scope denial: user=%s project=%s not in %d visible",
            user_id, project_id, len(visible),
        )
        raise HTTPException(status_code=404, detail="not found")
    return project


def require_project_access(param: str = "project_id"):
    """Router-level dependency enforcing `assert_can_read_project` on `{param}`.

    Stashes the resolved project on `request.state.project` so a handler that needs it
    does not repeat the lookup.

    A route in a router carrying this dependency that has NO `{project_id}` path
    parameter passes through untouched — there is no project to scope to, and raising
    would break sibling routes for no security gain.
    """

    async def _dep(
        request: Request, db: AsyncSession = Depends(get_db_session)
    ) -> None:
        project_id = request.path_params.get(param)
        if not project_id:
            return
        request.state.project = await assert_can_read_project(db, request, project_id)

    return _dep


async def project_admin_tier(
    db: AsyncSession, request: Request, project: Any
) -> str | None:
    """WHICH tier the caller runs this project from: "org", "unit", "project", or None.

    Same three tests as `assert_can_administer_project` — which delegates here, so
    the two can never disagree about who administers a project — but it reports the
    answer rather than only accepting or refusing it.

    The distinction matters where a write's CONSEQUENCE depends on the tier and not
    just on permission: a Project Admin editing their own project's settings raises
    a request for their Business Unit Admin, while that BU Admin (or an Org Admin)
    applies the same edit directly. Both "may administer the project"; only one of
    them is the person the request would be routed to.
    """
    if is_org_wide(request):
        return "org"

    administered = await administered_workspace_ids(db, request)
    if administered is not None and str(project.workspace_id) in administered:
        return "unit"

    user_id = getattr(request.state, "user_id", "") or ""
    own = (
        await db.execute(
            text(
                f"SELECT 1 FROM role_bindings rb "
                f"WHERE {live_binding()} AND rb.scope_kind = 'project' "
                f"  AND rb.scope_id = :p AND rb.role_name = 'project_admin'"
            ),
            {"u": user_id, "p": project.id, "now": datetime.now(tz=timezone.utc)},
        )
    ).first()
    return "project" if own is not None else None


async def assert_can_administer_project(
    db: AsyncSession, request: Request, project: Any
) -> None:
    """Refuse a write aimed at a project the caller does not run.

    Org-wide callers pass. Everyone else must administer the project's PARENT UNIT or
    hold a `project_admin` binding on the project itself — the two ways someone
    legitimately runs a project.

    Promoted here from `project_members.py`, which had it as a private helper, so the
    project settings write can enforce the same rule rather than inventing a second one.

    404, not 403, deliberately: refusing without confirming the project exists to
    somebody who does not run it.
    """
    if await project_admin_tier(db, request, project) is None:
        raise HTTPException(status_code=404, detail="not found")
