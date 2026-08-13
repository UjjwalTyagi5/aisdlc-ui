"""Retune what a built-in role grants, and read the permission catalogue.

THE ROLES PAGE WRITES HERE. `role_permissions` is the shipped default and is reconciled
from code on every boot, so it is not writable; this writes `role_permission_overrides`,
which the seeder never touches. See migration 0011.

ORGANIZATION ADMIN ONLY, and the check is org-wideness rather than a permission string.
Changing what a role grants is how you would grant yourself anything — a `role:manage`
holder is a Business Unit Admin, who administers people WITHIN their unit and has no
standing to redefine what "Developer" means for the whole organization.

TWO ROLES ARE NOT EDITABLE, and refusing them is not tidiness:
  org_admin  holds `admin:*`. Removing it locks every administrator out of the
             product with no way back in through the UI that just removed it.
  custom     is the placeholder a custom-role binding carries. Its permissions live
             on the custom role itself; editing this row would change nothing and
             look like it had.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.audit.models import AuditEventPayload
from shared.audit.service import audit_service
from shared.authz.dependency import require_permission
from shared.authz.read_scope import is_org_wide
from shared.authz.role_permissions import overrides, shipped_defaults
from shared.db import get_db_session

logger = logging.getLogger(__name__)

role_permissions_router = APIRouter(
    prefix="/admin",
    dependencies=[Depends(require_permission("artifact:view"))],
)

# See the module docstring. Kept as data rather than inline conditions so the reason
# a role is locked is answerable from one place.
NOT_EDITABLE: dict[str, str] = {
    "org_admin": (
        "The Organization Admin holds every permission by wildcard. Editing it could "
        "lock every administrator out of the product."
    ),
    "custom": (
        "This is the placeholder a custom-role binding carries. Edit the custom role "
        "itself to change what it grants."
    ),
}


class RolePermissionRow(BaseModel):
    """Mirrors `RolePermissionRow` in frontend/lib/api/role-permissions.ts."""

    role: str
    defaults: list[str]
    effective: list[str]
    overridden: bool
    editable: bool
    # Present only when `editable` is false — the panel shows it instead of a
    # disabled control with no explanation.
    lockedReason: Optional[str] = None


class RolePermissionSaveIn(BaseModel):
    role: str = Field(min_length=1, max_length=64)
    # The WHOLE set this role should hold. Not a delta — see migration 0011.
    permissions: Optional[list[str]] = None
    # Put it back to what shipped: deletes the override rows entirely.
    reset: bool = False


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


def _require_org_admin(request: Request) -> None:
    if not is_org_wide(request):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "forbidden",
                "message": (
                    "Only an Organization Admin can change what a built-in role grants."
                ),
            },
        )


async def _rows(db: AsyncSession, tenant_id: str) -> list[RolePermissionRow]:
    defaults = await shipped_defaults(db)
    tuned = await overrides(db, tenant_id)
    out: list[RolePermissionRow] = []
    for role in sorted(defaults):
        locked = NOT_EDITABLE.get(role)
        out.append(
            RolePermissionRow(
                role=role,
                defaults=sorted(defaults[role]),
                effective=sorted(tuned.get(role, defaults[role])),
                overridden=role in tuned,
                editable=locked is None,
                lockedReason=locked,
            )
        )
    return out


@role_permissions_router.get("/role-permissions", response_model=list[RolePermissionRow])
async def list_role_permissions(
    request: Request, db: AsyncSession = Depends(get_db_session)
) -> list[RolePermissionRow]:
    """Every built-in role: what it ships with, what it holds here, whether they differ.

    Readable by anyone signed in, deliberately. "What can this role do" is the question
    a person asks about their OWN role, and answering it only for administrators makes
    the permission model something you have to be told rather than look up. WRITING is
    the org admin's.
    """
    return await _rows(db, _tenant_id(request))


@role_permissions_router.put("/role-permissions", response_model=RolePermissionRow)
async def save_role_permissions(
    request: Request,
    body: RolePermissionSaveIn,
    db: AsyncSession = Depends(get_db_session),
) -> RolePermissionRow:
    """Take ownership of a built-in role's permission set, or hand it back."""
    tenant_id = _tenant_id(request)
    _require_org_admin(request)

    defaults = await shipped_defaults(db)
    if body.role not in defaults:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": f"No role '{body.role}'."}
        )
    if body.role in NOT_EDITABLE:
        raise HTTPException(
            status_code=422,
            detail={"code": "role_not_editable", "message": NOT_EDITABLE[body.role]},
        )

    actor = getattr(request.state, "user_id", "") or None

    if body.reset:
        await db.execute(
            text(
                "DELETE FROM role_permission_overrides "
                "WHERE tenant_id = CAST(:t AS uuid) AND role_name = :r"
            ),
            {"t": tenant_id, "r": body.role},
        )
        await _audit(db, tenant_id, actor, body.role, "reset", sorted(defaults[body.role]))
        await db.flush()
        return _one(await _rows(db, tenant_id), body.role)

    requested = sorted(set(body.permissions or []))
    if not requested:
        # A built-in role granting nothing is not a configuration, it is a role
        # nobody can use — and `contributor` already exists as the read-only floor
        # for exactly that case. Refusing is clearer than storing it and having the
        # holder discover it.
        raise HTTPException(
            status_code=422,
            detail={
                "code": "empty_permission_set",
                "message": (
                    "A role must grant at least one permission. To give someone nothing "
                    "yet, assign the Contributor role instead."
                ),
            },
        )

    known = {
        r[0]
        for r in (await db.execute(text("SELECT name FROM permissions"))).fetchall()
    }
    unknown = [p for p in requested if p not in known]
    if unknown:
        # The FK would refuse these anyway; catching them here names all of them at
        # once instead of failing on whichever the database reached first.
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unknown_permission",
                "message": f"Not in the permission catalogue: {', '.join(unknown)}.",
            },
        )

    # Replace wholesale. Delete-then-insert rather than a diff: the request states the
    # complete set, so computing a delta would only add a way for the stored set to
    # end up as neither the old one nor the new one.
    await db.execute(
        text(
            "DELETE FROM role_permission_overrides "
            "WHERE tenant_id = CAST(:t AS uuid) AND role_name = :r"
        ),
        {"t": tenant_id, "r": body.role},
    )
    for permission in requested:
        await db.execute(
            text(
                "INSERT INTO role_permission_overrides "
                "  (tenant_id, role_name, permission_name, updated_by) "
                "VALUES (CAST(:t AS uuid), :r, :p, :by)"
            ),
            {"t": tenant_id, "r": body.role, "p": permission, "by": actor},
        )

    await _audit(db, tenant_id, actor, body.role, "updated", requested)
    await db.flush()

    logger.info(
        "role permissions overridden: tenant=%s role=%s by=%s -> %s",
        tenant_id, body.role, actor, requested,
    )
    return _one(await _rows(db, tenant_id), body.role)


@role_permissions_router.get("/permissions")
async def list_permissions(
    request: Request, db: AsyncSession = Depends(get_db_session)
) -> list[dict[str, Any]]:
    """The permission catalogue, FROM THE DATABASE.

    This is what makes adding a permission a data change rather than a release: insert
    a row into `permissions`, and it becomes selectable on the Roles page and grantable
    to a custom role without a frontend deploy. The UI labels the ones it recognises
    and falls back to the raw id for the rest, so a new permission is usable the moment
    it exists and merely looks plainer until someone writes a label for it.

    `admin:*` and any other wildcard is excluded: wildcards satisfy every check, so
    offering one in a picker is offering "make this role an administrator" disguised as
    a checkbox.
    """
    _tenant_id(request)
    rows = (
        await db.execute(text("SELECT name FROM permissions ORDER BY name"))
    ).fetchall()
    return [{"id": r[0]} for r in rows if not r[0].endswith(":*")]


def _one(rows: list[RolePermissionRow], role: str) -> RolePermissionRow:
    for row in rows:
        if row.role == role:
            return row
    raise HTTPException(status_code=404, detail={"code": "not_found", "message": role})


async def _audit(
    db: AsyncSession,
    tenant_id: str,
    actor: Optional[str],
    role: str,
    action: str,
    permissions: list[str],
) -> None:
    """Record the change in the append-only trail.

    Redefining a role changes what every one of its holders may do, without touching
    a single binding — so it is invisible in the assignment history that would
    otherwise be the place to look.
    """
    await audit_service.emit(
        AuditEventPayload(
            tenant_id=tenant_id,
            event_type=f"role_permissions.{action}",
            actor_id=actor,
            resource_type="role",
            resource_id=role,
            payload={"role": role, "permissions": permissions},
        )
    )
