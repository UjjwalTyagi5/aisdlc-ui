"""A project's roster — who works on it, and in which role.

THE STORAGE ALWAYS EXISTED. `role_bindings.scope_kind` admits `project`, and
`can_perform` has always resolved permissions through project-scope rows. What was
missing was any way to read or write them, so a project's roster was unanswerable
through the API even though the database could express it.

WHY IT IS NOT `/workspaces/{id}/members` WITH A DIFFERENT SCOPE. A unit's roster and a
project's are different questions with different writers: a Business Unit Admin admits
people to their unit, a Project Admin staffs their own project from people already
admitted. Sharing an endpoint would mean one permission gate for two decisions, and the
narrower one would win or lose for both.

WRITES ARE BOUNDED BY THE PROJECT, not by the unit. `member:manage` says the caller may
manage members somewhere; `_assert_can_write_project` says which project — otherwise a
Project Admin could staff a sibling project by passing its id, which is the same hole
`assert_can_write_workspace` was written to close one level up.
"""
from __future__ import annotations

import json
import logging
import re
import uuid as _uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.dependency import require_permission
from shared.authz.permissions import ALL_ROLES
from shared.authz.read_scope import administered_workspace_ids, is_org_wide
from shared.db import get_db_session

logger = logging.getLogger(__name__)

project_members_router = APIRouter(
    prefix="/projects",
    dependencies=[Depends(require_permission("artifact:view"))],
)


class MemberCreateIn(BaseModel):
    """An EXISTING person's email plus the role they take on this project.

    Deliberately does not onboard: creating an account is an Organization Admin act
    (see /onboarding), and letting a project roster do it would put account creation
    behind `member:manage`, which a Project Admin holds.
    """

    email: EmailStr
    roleName: str = Field(min_length=1, max_length=64)
    extraAgents: Optional[list[str]] = None


class MemberPatchIn(BaseModel):
    roleName: Optional[str] = Field(default=None, min_length=1, max_length=64)
    extraAgents: Optional[list[str]] = None


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


def _initials(display_name: Optional[str], email: Optional[str], user_id: str) -> str:
    source = display_name or (email.split("@", 1)[0] if email else "") or user_id
    parts = [p for p in re.split(r"[.\s_\-+]+", source) if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (source[:2] or "?").upper()


def _display_name(email: Optional[str], user_id: str) -> str:
    local = email.split("@", 1)[0] if email else ""
    if not local:
        return user_id
    parts = [p for p in re.split(r"[._\-+]+", local) if p]
    return " ".join(p[:1].upper() + p[1:] for p in parts) or user_id


async def _project_or_404(db: AsyncSession, tenant_id: str, project_id: str) -> Any:
    try:
        pid = str(_uuid.UUID(project_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=422, detail="project_id must be a UUID")
    row = (
        await db.execute(
            text(
                "SELECT id, workspace_id, display_name FROM projects "
                "WHERE id = CAST(:p AS uuid) AND tenant_id = CAST(:t AS uuid)"
            ),
            {"p": pid, "t": tenant_id},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return row


async def _assert_can_write_project(db: AsyncSession, request: Request, project: Any) -> None:
    """Refuse a write aimed at a project the caller does not run.

    Org-wide callers pass. Everyone else must administer the project's PARENT UNIT or
    hold a project_admin binding on the project itself — the two ways someone
    legitimately staffs a roster.

    404 rather than 403, matching the sibling routes: a project the caller cannot
    reach should not be confirmed to exist by the error code.
    """
    if is_org_wide(request):
        return

    administered = await administered_workspace_ids(db, request)
    if administered is not None and str(project.workspace_id) in administered:
        return

    user_id = getattr(request.state, "user_id", "") or ""
    own = (
        await db.execute(
            text(
                "SELECT 1 FROM role_bindings WHERE user_id = :u AND scope_kind = 'project' "
                "  AND scope_id = :p AND role_name = 'project_admin' "
                "  AND status <> 'deactivated'"
            ),
            {"u": user_id, "p": project.id},
        )
    ).first()
    if own is None:
        raise HTTPException(status_code=404, detail="not found")


async def _rows(db: AsyncSession, project: Any) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT rb.id, rb.user_id, rb.role_name, rb.status, rb.created_at, "
                "       rb.extra_agents, u.email "
                "FROM role_bindings rb "
                "LEFT JOIN users u ON u.id = rb.user_id "
                "WHERE rb.scope_kind = 'project' AND rb.scope_id = :p "
                "ORDER BY rb.created_at ASC"
            ),
            {"p": project.id},
        )
    ).fetchall()

    # A person's HOME unit, named only when it is not this project's own — so the
    # roster stays quiet about the unit everyone shares and speaks up about the one
    # person who is a guest. Whose headcount they are, and whose admin to go back to,
    # is what a Project Admin needs and cannot otherwise see.
    home: dict[str, str] = {}
    if rows:
        unit_rows = (
            await db.execute(
                text(
                    "SELECT rb.user_id, w.id, w.display_name FROM role_bindings rb "
                    "JOIN workspaces w ON w.id = rb.scope_id "
                    "WHERE rb.scope_kind = 'business_unit' AND rb.status <> 'deactivated' "
                    "  AND rb.user_id = ANY(:users)"
                ),
                {"users": [r.user_id for r in rows]},
            )
        ).fetchall()
        for user_id, unit_id, unit_name in unit_rows:
            if str(unit_id) != str(project.workspace_id):
                home[user_id] = unit_name

    out: list[dict[str, Any]] = []
    for r in rows:
        name = _display_name(r.email, r.user_id)
        out.append(
            {
                "membershipId": str(r.id),
                "projectId": str(project.id),
                "identity": {
                    "id": r.user_id,
                    "ssoSubject": r.user_id,
                    "email": r.email,
                    "displayName": name,
                    "initials": _initials(name, r.email, r.user_id),
                    "idpSource": None,
                    "links": [],
                },
                "role": r.role_name,
                "status": r.status,
                "addedAt": r.created_at.isoformat() if r.created_at else None,
                "extraAgents": r.extra_agents or [],
                "homeBusinessUnitName": home.get(r.user_id),
            }
        )
    return out


@project_members_router.get("/{project_id}/members")
async def list_project_members(
    project_id: str, request: Request, db: AsyncSession = Depends(get_db_session)
) -> list[dict[str, Any]]:
    """The roster. Readable by anyone who can see the project — you work with these
    people, so who else is on it is not privileged information."""
    project = await _project_or_404(db, _tenant_id(request), project_id)
    return await _rows(db, project)


@project_members_router.post(
    "/{project_id}/members",
    status_code=201,
    dependencies=[Depends(require_permission("member:manage"))],
)
async def add_project_member(
    project_id: str,
    body: MemberCreateIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    tenant_id = _tenant_id(request)
    project = await _project_or_404(db, tenant_id, project_id)
    await _assert_can_write_project(db, request, project)

    if body.roleName not in ALL_ROLES:
        raise HTTPException(status_code=422, detail=f"Unknown role '{body.roleName}'")

    user = (
        await db.execute(
            text(
                "SELECT id FROM users WHERE lower(email) = :e AND tenant_id = CAST(:t AS uuid)"
            ),
            {"e": body.email.lower(), "t": tenant_id},
        )
    ).first()
    if user is None:
        # Not "invite them" — creating an account is an Organization Admin act, and
        # doing it here would put account creation behind member:manage.
        raise HTTPException(
            status_code=404,
            detail={
                "code": "no_such_person",
                "message": (
                    f"Nobody in this organisation uses {body.email}. They have to be "
                    "onboarded before they can join a project."
                ),
            },
        )

    from shared.authz.grant import grant_role  # noqa: PLC0415 - avoids an import cycle

    try:
        await grant_role(
            user.id, str(project.id), body.roleName,
            tenant_id=tenant_id, scope_kind="project",
            granted_by=getattr(request.state, "user_id", None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if body.extraAgents:
        await db.execute(
            text(
                "UPDATE role_bindings SET extra_agents = CAST(:a AS jsonb) "
                "WHERE user_id = :u AND scope_kind = 'project' AND scope_id = :p "
                "  AND role_name = :r"
            ),
            {
                "a": json.dumps(body.extraAgents),
                "u": user.id, "p": project.id, "r": body.roleName,
            },
        )
    await db.flush()

    rows = await _rows(db, project)
    match = next((m for m in rows if m["identity"]["id"] == user.id), None)
    if match is None:
        raise HTTPException(status_code=500, detail="membership was not created")
    return match


@project_members_router.patch(
    "/{project_id}/members/{membership_id}",
    dependencies=[Depends(require_permission("member:manage"))],
)
async def update_project_member(
    project_id: str,
    membership_id: str,
    body: MemberPatchIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    tenant_id = _tenant_id(request)
    project = await _project_or_404(db, tenant_id, project_id)
    await _assert_can_write_project(db, request, project)

    if body.roleName is not None and body.roleName not in ALL_ROLES:
        raise HTTPException(status_code=422, detail=f"Unknown role '{body.roleName}'")

    sets, params = [], {"m": membership_id, "p": project.id}
    if body.roleName is not None:
        sets.append("role_name = :r")
        params["r"] = body.roleName
    if body.extraAgents is not None:
        sets.append("extra_agents = CAST(:a AS jsonb)")
        params["a"] = json.dumps(body.extraAgents)
    if not sets:
        raise HTTPException(status_code=422, detail="Nothing to change")

    # Guarded on scope_id as well as id: a membership id from another project must
    # not be editable by passing this project's id in the path.
    result = await db.execute(
        text(
            f"UPDATE role_bindings SET {', '.join(sets)} "
            "WHERE id = CAST(:m AS uuid) AND scope_kind = 'project' AND scope_id = :p"
        ),
        params,
    )
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="not found")
    await db.flush()

    rows = await _rows(db, project)
    match = next((m for m in rows if m["membershipId"] == str(membership_id)), None)
    if match is None:
        raise HTTPException(status_code=404, detail="not found")
    return match


@project_members_router.delete(
    "/{project_id}/members/{membership_id}",
    status_code=204,
    response_class=Response,
    dependencies=[Depends(require_permission("member:manage"))],
)
async def remove_project_member(
    project_id: str,
    membership_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    tenant_id = _tenant_id(request)
    project = await _project_or_404(db, tenant_id, project_id)
    await _assert_can_write_project(db, request, project)

    await db.execute(
        text(
            "DELETE FROM role_bindings WHERE id = CAST(:m AS uuid) "
            "  AND scope_kind = 'project' AND scope_id = :p"
        ),
        {"m": membership_id, "p": project.id},
    )
    await db.flush()
    # 204 whether or not a row went: removing someone already gone satisfies the
    # caller's intent, and a 404 here reads as "wrong project".
    return Response(status_code=204)
