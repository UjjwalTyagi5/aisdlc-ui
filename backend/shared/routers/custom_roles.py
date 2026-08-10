"""Custom-role CRUD — org admins define tenant-scoped roles (role:manage, D-1 hybrid).

Tenant isolation is enforced by FORCE RLS on custom_roles / custom_role_permissions:
all reads/writes go through get_db_session (request-scoped, sets app.current_tenant_id).
Custom roles are composed only from the global permission catalog; wildcards
(admin:* / platform:*) are NOT grantable to a custom role.
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
from shared.authz.permissions import ALL_PERMISSIONS
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


class CustomRoleIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str] = Field(default_factory=list)


class CustomRoleOut(BaseModel):
    id: str
    name: str
    description: str | None
    permissions: list[str]


class OkOut(BaseModel):
    ok: bool = True


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


@custom_roles_router.post("", response_model=CustomRoleOut, status_code=201)
async def create_custom_role(
    request: Request, body: CustomRoleIn, db: AsyncSession = Depends(get_db_session)
) -> CustomRoleOut:
    tenant_id = _tenant_id(request)
    try:
        _validate_permissions(body.permissions)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    role_id = _uuid.uuid4()
    try:
        await db.execute(
            text(
                "INSERT INTO custom_roles (id, tenant_id, name, description) "
                "VALUES (:id, :t, :name, :descr)"
            ),
            {"id": str(role_id), "t": tenant_id, "name": body.name, "descr": body.description},
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
    return CustomRoleOut(
        id=str(role_id), name=body.name, description=body.description, permissions=body.permissions
    )


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
            CustomRoleOut(id=str(r.id), name=r.name, description=r.description, permissions=list(perms))
        )
    return out


@custom_roles_router.delete("/{role_id}", response_model=OkOut)
async def delete_custom_role(
    role_id: str, request: Request, db: AsyncSession = Depends(get_db_session)
) -> OkOut:
    _tenant_id(request)
    try:
        rid = _uuid.UUID(role_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="role_id must be a UUID")
    await db.execute(text("DELETE FROM custom_roles WHERE id = :rid"), {"rid": str(rid)})
    return OkOut()
