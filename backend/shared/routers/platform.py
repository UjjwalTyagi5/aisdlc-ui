"""Platform-tier endpoints (Application Team). platform:*-gated, cross-tenant.

Phase 3 ships a single read-only proof: list all organizations across tenants via
the BYPASSRLS/superuser session. Full org CRUD lands in Phase 4. Platform admins are
tenant-LESS (tenant_id=""), so we gate on the platform:* permission directly rather
than require_permission (which does resource-less workspace resolution unfit for a
tenant-less caller: resolve_default_workspace("") raises 404 — swallowed today, but
relying on that fall-through is fragile; the explicit gate mirrors admin._require_admin).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from shared.auth.platform_identity import PLATFORM_WILDCARD
from shared.authz.permissions import has_permission
from shared.db import get_db_session_superuser


async def require_platform_admin(request: Request) -> None:
    perms = getattr(request.state, "permissions", []) or []
    if not has_permission(perms, PLATFORM_WILDCARD):
        raise HTTPException(status_code=403, detail="Forbidden")


require_platform_admin.__rbac_require_permission__ = True  # D-05 boot-scan sentinel

platform_router = APIRouter(prefix="/platform", dependencies=[Depends(require_platform_admin)])


class OrgOut(BaseModel):
    id: str
    slug: str
    display_name: str
    suspended: bool
    member_count: int
    run_count: int


@platform_router.get("/organizations", response_model=list[OrgOut])
async def list_all_orgs() -> list[OrgOut]:
    async with get_db_session_superuser() as s:
        rows = (await s.execute(text(
            "SELECT o.id, o.slug, o.display_name, o.suspended, "
            "  (SELECT COUNT(DISTINCT user_id) FROM user_workspace_roles uwr "
            "     WHERE uwr.tenant_id = o.id) AS member_count, "
            "  (SELECT COUNT(*) FROM runs r WHERE r.tenant_id = o.id) AS run_count "
            "FROM organizations o ORDER BY o.display_name"
        ))).fetchall()
    return [OrgOut(id=str(r.id), slug=r.slug, display_name=r.display_name,
                   suspended=bool(r.suspended), member_count=int(r.member_count),
                   run_count=int(r.run_count)) for r in rows]


class ProvisionOrgIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=128)
    admin_email: EmailStr
    admin_password: str = Field(min_length=8, max_length=200)


class ProvisionOrgOut(BaseModel):
    org_id: str
    slug: str
    display_name: str
    workspace_id: str
    admin_email: str
    admin_user_id: str


@platform_router.post("/organizations", response_model=ProvisionOrgOut, status_code=201)
async def create_organization(body: ProvisionOrgIn) -> ProvisionOrgOut:
    from shared.services.provisioning import (
        ProvisioningError, SlugTakenError, provision_organization,
    )
    try:
        result = await provision_organization(
            body.display_name, body.slug, body.admin_email, body.admin_password
        )
    except SlugTakenError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ProvisioningError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return ProvisionOrgOut(**result)


# ---------------------------------------------------------------------------
# Phase 5 — org detail, suspend, reset-admin-password, platform-user CRUD
# ---------------------------------------------------------------------------

class OrgDetailOut(BaseModel):
    org_id: str
    slug: str
    display_name: str
    suspended: bool
    workspaces: list[dict]
    admins: list[dict]
    members: list[dict]
    member_count: int
    run_count: int
    total_cost_usd: float


class SuspendIn(BaseModel):
    suspended: bool


class ResetAdminPwIn(BaseModel):
    email: EmailStr
    new_password: str = Field(min_length=8, max_length=200)


class PlatformUserIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    platform_role: str = Field(pattern="^(platform_admin|platform_support)$")


class PlatformUserOut(BaseModel):
    user_id: str
    email: str
    platform_role: str
    active: bool


class ActiveIn(BaseModel):
    active: bool


@platform_router.get("/organizations/{org_id}", response_model=OrgDetailOut)
async def get_organization(org_id: str) -> OrgDetailOut:
    from shared.services.platform_admin import get_org_detail, OrgNotFoundError
    try:
        return OrgDetailOut(**await get_org_detail(org_id))
    except OrgNotFoundError:
        raise HTTPException(status_code=404, detail="Organization not found")


@platform_router.patch("/organizations/{org_id}", status_code=204, response_model=None)
async def patch_organization(org_id: str, body: SuspendIn) -> None:
    from shared.services.platform_admin import set_org_suspended, OrgNotFoundError
    try:
        await set_org_suspended(org_id, body.suspended)
    except OrgNotFoundError:
        raise HTTPException(status_code=404, detail="Organization not found")


@platform_router.post("/organizations/{org_id}/reset-admin-password", status_code=204, response_model=None)
async def reset_admin_password(org_id: str, body: ResetAdminPwIn) -> None:
    from shared.services.platform_admin import reset_org_admin_password, AdminNotFoundError
    try:
        await reset_org_admin_password(org_id, body.email, body.new_password)
    except AdminNotFoundError:
        raise HTTPException(status_code=404, detail="No org_admin with that email in this org")


@platform_router.get("/users", response_model=list[PlatformUserOut])
async def list_platform_users_route() -> list[PlatformUserOut]:
    from shared.services.platform_admin import list_platform_users
    return [PlatformUserOut(**u) for u in await list_platform_users()]


@platform_router.post("/users", response_model=PlatformUserOut, status_code=201)
async def create_platform_user_route(body: PlatformUserIn) -> PlatformUserOut:
    from shared.services.platform_admin import create_platform_user, PlatformUserExistsError
    try:
        return PlatformUserOut(**await create_platform_user(
            body.email, body.password, body.platform_role))
    except PlatformUserExistsError:
        raise HTTPException(status_code=409, detail="Platform user already exists")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@platform_router.patch("/users/{user_id}", status_code=204, response_model=None)
async def patch_platform_user(user_id: str, body: ActiveIn, request: Request) -> None:
    from shared.services.platform_admin import set_platform_user_active, PlatformUserNotFoundError
    if getattr(request.state, "user_id", None) == user_id and not body.active:
        raise HTTPException(status_code=409, detail="Cannot deactivate your own account")
    try:
        await set_platform_user_active(user_id, body.active)
    except PlatformUserNotFoundError:
        raise HTTPException(status_code=404, detail="Platform user not found")
