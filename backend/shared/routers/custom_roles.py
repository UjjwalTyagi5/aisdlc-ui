"""Custom-role CRUD — tenant-defined roles composed from the permission catalogue.

Tenant isolation is enforced by FORCE RLS on custom_roles / custom_role_permissions:
all reads/writes go through get_db_session (request-scoped, sets app.current_tenant_id).
Custom roles are composed only from the global permission catalog; wildcards
(admin:* / platform:*) are NOT grantable to a custom role.

NO ESCALATION: a creator cannot put a permission into a custom role that they do not
themselves hold. Without that rule this endpoint was a straightforward privilege
escalation — `role:manage` is held by a Business Unit Admin, and any catalogue
permission could be packaged into a role and then assigned, including to themselves.
The catalogue check alone only ever asked whether a permission EXISTS.

The creator's permissions are re-resolved from the database rather than read from the
JWT. Everywhere else the token is the authority, and rightly so; here it is not, because
a custom role OUTLIVES the session that created it. A token issued before a demotion
stays valid for its lifetime, and using it would let a just-demoted admin mint a role
carrying the permissions they no longer have — a durable grant from a stale claim.

OWNER SCOPE: an organization-scoped role is assignable anywhere in the tenant; a
business-unit-scoped role only within the unit that owns it. That is what lets a BU
Admin define a role for their own unit without defining it for every unit.
"""
from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.dependency import require_permission
from shared.authz.permissions import ALL_PERMISSIONS, has_permission
from shared.authz.read_scope import assert_can_write_workspace, is_org_wide
from shared.authz.resolver import resolve_permissions_for_user
from shared.authz.token_epoch import bump_many


async def _holders_of_custom_role(db, custom_role_id: str) -> list[str]:
    """Everyone bound to this custom role, for stale-token invalidation."""
    rows = (
        await db.execute(
            text(
                "SELECT DISTINCT user_id FROM role_bindings "
                "WHERE custom_role_id = CAST(:rid AS uuid)"
            ),
            {"rid": custom_role_id},
        )
    ).fetchall()
    return [r[0] for r in rows]
from shared.db import get_db_session
from shared.models.orm import CustomRole

logger = logging.getLogger(__name__)

custom_roles_router = APIRouter(
    prefix="/admin/custom-roles",
    dependencies=[Depends(require_permission("role:manage"))],
)


def _validate_permissions(perms: list[str]) -> None:
    """Reject unknown permissions and wildcards. ALL_PERMISSIONS is the leaf catalog."""
    catalog = set(ALL_PERMISSIONS)
    for p in perms:
        if p.endswith(":*") or p not in catalog:
            raise ValueError(f"permission not grantable to a custom role: {p!r}")


async def _assert_creator_holds(request: Request, requested: list[str]) -> None:
    """Refuse to package a permission the caller does not hold. 403 on any excess.

    `admin:*` passes everything by design — the wildcard IS the full catalogue, so an
    Organization Admin composing any role is not an escalation.

    The excess permissions ARE named in the error, unlike the deliberately opaque 403
    elsewhere. They are the caller's own permissions being described back to them, so
    there is nothing to disclose, and "forbidden" with no indication of which of
    fifteen ticked boxes was the problem is unusable.
    """
    if not requested:
        # Nothing requested, nothing to escalate. Returning early also spares a DB
        # round trip on the empty-role case, which the UI issues while a form is
        # still being filled in.
        return

    user_id = getattr(request.state, "user_id", "") or ""
    tenant_id = getattr(request.state, "tenant_id", "") or ""
    if not user_id or not tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    held = await resolve_permissions_for_user(user_id, tenant_id)
    if has_permission(held, "admin:*"):
        return

    held_set = set(held)
    excess = sorted(p for p in requested if p not in held_set)
    if excess:
        logger.warning(
            "custom role creation refused: user=%s tried to grant permissions they lack: %s",
            user_id, excess,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                "You cannot grant permissions you do not hold: "
                + ", ".join(excess)
            ),
        )


class CustomRoleIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str] = Field(default_factory=list)


class CustomRoleOut(BaseModel):
    id: str
    name: str
    description: str | None
    permissions: list[str]
    scopeKind: str = "organization"
    scopeId: str | None = None
    createdBy: str | None = None


class OkOut(BaseModel):
    ok: bool = True


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


async def _create(
    request: Request,
    body: CustomRoleIn,
    db: AsyncSession,
    *,
    scope_kind: str,
    scope_id: str,
) -> CustomRoleOut:
    """Shared creation path for both the org-scoped and unit-scoped endpoints.

    One function so the two can never diverge on the checks that matter — the
    catalogue validation and, above all, the no-escalation rule.
    """
    tenant_id = _tenant_id(request)
    try:
        _validate_permissions(body.permissions)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    await _assert_creator_holds(request, body.permissions)

    created_by = getattr(request.state, "user_id", "") or None
    role_id = _uuid.uuid4()
    try:
        await db.execute(
            text(
                "INSERT INTO custom_roles "
                "  (id, tenant_id, name, description, scope_kind, scope_id, created_by) "
                "VALUES (:id, :t, :name, :descr, :sk, :sid, :by)"
            ),
            {
                "id": str(role_id), "t": tenant_id, "name": body.name,
                "descr": body.description, "sk": scope_kind, "sid": scope_id,
                "by": created_by,
            },
        )
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"role '{body.name}' already exists")
    for p in body.permissions:
        await db.execute(
            text(
                "INSERT INTO custom_role_permissions (id, custom_role_id, permission_name, tenant_id) "
                "VALUES (:id, :rid, :p, :t)"
            ),
            {"id": str(_uuid.uuid4()), "rid": str(role_id), "p": p, "t": tenant_id},
        )
    logger.info(
        "custom role created: name=%s scope=%s:%s by=%s perms=%d",
        body.name, scope_kind, scope_id, created_by, len(body.permissions),
    )
    return CustomRoleOut(
        id=str(role_id), name=body.name, description=body.description,
        permissions=body.permissions, scopeKind=scope_kind, scopeId=scope_id,
        createdBy=created_by,
    )


@custom_roles_router.post("", response_model=CustomRoleOut, status_code=201)
async def create_custom_role(
    request: Request, body: CustomRoleIn, db: AsyncSession = Depends(get_db_session)
) -> CustomRoleOut:
    """Create an ORGANIZATION-scoped custom role — assignable anywhere in the tenant.

    Org-wide authority required. `role:manage` alone is not enough: a Business Unit
    Admin holds it for their own unit, and a role assignable across every unit is not
    theirs to define. They use the unit-scoped endpoint below instead.
    """
    tenant_id = _tenant_id(request)
    if not is_org_wide(request):
        raise HTTPException(
            status_code=403,
            detail=(
                "Only an Organization Admin can create an organization-wide role. "
                "Create it for your business unit instead."
            ),
        )
    return await _create(
        request, body, db, scope_kind="organization", scope_id=tenant_id
    )


@custom_roles_router.post(
    "/business-unit/{workspace_id}", response_model=CustomRoleOut, status_code=201
)
async def create_bu_custom_role(
    workspace_id: str,
    request: Request,
    body: CustomRoleIn,
    db: AsyncSession = Depends(get_db_session),
) -> CustomRoleOut:
    """Create a role owned by one business unit, assignable only inside it.

    Guarded by the same write check as every other unit-scoped write, so a BU Admin
    cannot define a role inside a unit they do not administer.
    """
    _tenant_id(request)
    try:
        unit = str(_uuid.UUID(workspace_id))
    except ValueError:
        raise HTTPException(status_code=422, detail="workspace_id must be a UUID")

    await assert_can_write_workspace(db, request, unit)
    return await _create(request, body, db, scope_kind="business_unit", scope_id=unit)


@custom_roles_router.get("", response_model=list[CustomRoleOut])
async def list_custom_roles(
    request: Request, db: AsyncSession = Depends(get_db_session)
) -> list[CustomRoleOut]:
    _tenant_id(request)
    rows = (await db.execute(select(CustomRole).order_by(CustomRole.name))).scalars().all()
    out: list[CustomRoleOut] = []
    for r in rows:
        perms = (
            await db.execute(
                text("SELECT permission_name FROM custom_role_permissions WHERE custom_role_id = :rid"),
                {"rid": str(r.id)},
            )
        ).scalars().all()
        out.append(
            CustomRoleOut(
                id=str(r.id), name=r.name, description=r.description,
                permissions=list(perms),
                scopeKind=getattr(r, "scope_kind", "organization") or "organization",
                scopeId=str(r.scope_id) if getattr(r, "scope_id", None) else None,
                createdBy=getattr(r, "created_by", None),
            )
        )
    return out


@custom_roles_router.patch("/{role_id}", response_model=CustomRoleOut)
async def update_custom_role(
    role_id: str, request: Request, body: CustomRoleIn, db: AsyncSession = Depends(get_db_session)
) -> CustomRoleOut:
    """Rename a custom role or change what it grants.

    A ROLE BELONGS TO THE UNIT THAT DEFINED IT. A Business Unit Admin editing another
    unit's role — or the org-wide one every unit assigns — would change what people
    outside their authority are allowed to do, which is the escalation `scope_id`
    exists to prevent. So an org-scoped role needs org-wide authority, and a
    unit-scoped one needs write access to that unit.

    PERMISSIONS ARE REPLACED WHOLESALE, not merged. The request states the complete
    set, so computing a delta would only add a way for the stored set to end up as
    neither the old one nor the new one.
    """
    tenant_id = _tenant_id(request)
    try:
        rid = _uuid.UUID(role_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="role_id must be a UUID")

    row = (await db.execute(select(CustomRole).where(CustomRole.id == rid))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")

    scope_kind = getattr(row, "scope_kind", "organization") or "organization"
    if scope_kind == "business_unit":
        await assert_can_write_workspace(db, request, str(row.scope_id))
    elif not is_org_wide(request):
        raise HTTPException(
            status_code=403,
            detail=(
                "Only an Organization Admin can change an organization-wide role. It is "
                "assignable in every business unit."
            ),
        )

    row.name = body.name
    row.description = body.description
    await db.execute(
        text("DELETE FROM custom_role_permissions WHERE custom_role_id = :rid"),
        {"rid": str(rid)},
    )
    for permission in body.permissions:
        await db.execute(
            text(
                "INSERT INTO custom_role_permissions "
                "  (id, custom_role_id, permission_name, tenant_id) "
                "VALUES (CAST(:i AS uuid), CAST(:rid AS uuid), :p, CAST(:t AS uuid))"
            ),
            {"i": str(_uuid.uuid4()), "rid": str(rid), "p": permission, "t": tenant_id},
        )
    await db.flush()

    # Editing a custom role IS editing its holders' permissions — there is no shipped
    # default to fall back to — so every binding that carries it is now describing the
    # wrong set in its token.
    await bump_many(tenant_id, await _holders_of_custom_role(db, str(rid)))

    return CustomRoleOut(
        id=str(row.id), name=row.name, description=row.description,
        permissions=list(body.permissions), scopeKind=scope_kind,
        scopeId=str(row.scope_id) if row.scope_id else None,
        createdBy=getattr(row, "created_by", None),
    )


@custom_roles_router.delete("/{role_id}", response_model=OkOut)
async def delete_custom_role(
    role_id: str, request: Request, db: AsyncSession = Depends(get_db_session)
) -> OkOut:
    _tenant_id(request)
    try:
        rid = _uuid.UUID(role_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="role_id must be a UUID")
    # Collected BEFORE the delete: role_bindings cascades on custom_role_id, so after
    # this statement there is nobody left to look up and the bump would silently do
    # nothing — a deleted role is the sharpest permission removal there is.
    holders = await _holders_of_custom_role(db, str(rid))
    await db.execute(text("DELETE FROM custom_roles WHERE id = :rid"), {"rid": str(rid)})
    await bump_many(_tenant_id(request), holders)
    return OkOut()
