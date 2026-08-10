"""Workspaces resource router.

Real per-workspace CRUD + member management. A workspace is the business-unit
boundary inside an org: it owns its own projects, members, and model offerings.

Listing is scoped by membership for non-admin users (org_admin / workspace:manage
see everything; others see only workspaces they belong to). Mutations require
workspace:manage.

Returns camelCase shapes the frontend `Workspace` / `WorkspaceMember` schemas
expect — no BFF mapping needed.
"""
from __future__ import annotations

import re
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.dependency import require_permission
from shared.db import get_db_session
from shared.models.orm import Project, Role, UsageMonthly, User, UserWorkspaceRole, Workspace

workspaces_router = APIRouter()

# ─── helpers ────────────────────────────────────────────────────────────────

def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower().strip()).strip("-") or "workspace"


def _initials(display_name: str | None, email: str | None, user_id: str) -> str:
    if display_name:
        parts = display_name.strip().split()
        return ((parts[0][0] if parts else "") + (parts[1][0] if len(parts) > 1 else "")).upper() or "?"
    if email:
        local = email.split("@")[0]
        parts = local.replace(".", " ").replace("_", " ").split()
        return ((parts[0][0] if parts else "") + (parts[1][0] if len(parts) > 1 else "")).upper() or "?"
    return (user_id[:2]).upper() or "?"


def _is_admin_or_manager(request: Request) -> bool:
    perms: list[str] = getattr(request.state, "permissions", []) or []
    return "admin:*" in perms or "workspace:manage" in perms


def _can_view_cost(request: Request) -> bool:
    """Spend is financial data — only cost:view holders see the dollar figures."""
    perms: list[str] = getattr(request.state, "permissions", []) or []
    return "admin:*" in perms or "cost:view" in perms


# ─── Pydantic I/O models ─────────────────────────────────────────────────────

class WorkspaceOut(BaseModel):
    id: str
    organizationId: str
    slug: str
    displayName: str
    businessUnit: str | None
    costCenter: str | None
    dataClassification: str
    status: str
    memberCount: int
    projectCount: int
    monthlySpendUsd: float
    monthlyBudgetUsd: float | None
    createdAt: str


class WorkspaceCreateIn(BaseModel):
    displayName: str = Field(min_length=2, max_length=80)
    businessUnit: str | None = Field(default=None, max_length=120)
    costCenter: str | None = Field(default=None, max_length=64)
    dataClassification: str = Field(default="internal")
    monthlyBudgetUsd: float | None = Field(default=None, ge=0)


class WorkspacePatchIn(BaseModel):
    displayName: str | None = Field(default=None, max_length=80)
    businessUnit: str | None = Field(default=None, max_length=120)
    costCenter: str | None = Field(default=None, max_length=64)
    dataClassification: str | None = None
    status: str | None = None
    # A budget of 0 / null clears the cap (inherit parent / unlimited).
    monthlyBudgetUsd: float | None = Field(default=None, ge=0)


class WorkspaceMemberOut(BaseModel):
    userId: str
    email: str | None
    displayName: str | None
    initials: str
    roleName: str
    joinedAt: str


class AddMemberIn(BaseModel):
    """userId may be the auth sub (e.g. auth0|abc) OR an email address.
    The backend resolves by looking up in the users table (id first, then email)."""
    userId: str = Field(min_length=1, max_length=320)
    roleName: str = Field(min_length=1, max_length=64)


class UpdateMemberRoleIn(BaseModel):
    roleName: str = Field(min_length=1, max_length=64)


# ─── serialisers ─────────────────────────────────────────────────────────────

def _to_out(
    w: Workspace, member_count: int, project_count: int, spend_usd: float = 0.0
) -> WorkspaceOut:
    return WorkspaceOut(
        id=str(w.id),
        organizationId=str(w.organization_id),
        slug=w.slug,
        displayName=w.display_name,
        businessUnit=w.business_unit,
        costCenter=w.cost_center,
        dataClassification=w.data_classification,
        status=w.status,
        memberCount=member_count,
        projectCount=project_count,
        monthlySpendUsd=round(float(spend_usd or 0.0), 4),
        monthlyBudgetUsd=float(w.monthly_budget_usd) if w.monthly_budget_usd is not None else None,
        createdAt=w.created_at.isoformat() if w.created_at else "",
    )


async def _workspace_spend_map(db: AsyncSession, tenant_uuid: _uuid.UUID) -> dict[str, float]:
    """{workspace_id: current-calendar-month spend} from the durable usage rollup."""
    from shared.services.budget_store import month_key  # noqa: PLC0415

    rows = (
        await db.execute(
            select(UsageMonthly.scope_id, UsageMonthly.cost_usd).where(
                UsageMonthly.tenant_id == tenant_uuid,
                UsageMonthly.scope == "workspace",
                UsageMonthly.month == month_key(),
            )
        )
    ).all()
    return {str(sid): float(cost or 0.0) for sid, cost in rows}


# ─── count helpers ───────────────────────────────────────────────────────────

async def _counts(db: AsyncSession, tenant_uuid: _uuid.UUID) -> tuple[dict, dict]:
    """(members, projects) count maps keyed by workspace_id string."""
    proj_rows = (
        await db.execute(
            select(Project.workspace_id, func.count())
            .where(Project.tenant_id == tenant_uuid, Project.archived == False)  # noqa: E712
            .group_by(Project.workspace_id)
        )
    ).all()
    mem_rows = (
        await db.execute(
            select(UserWorkspaceRole.workspace_id, func.count(func.distinct(UserWorkspaceRole.user_id)))
            .group_by(UserWorkspaceRole.workspace_id)
        )
    ).all()
    projects = {str(r[0]): r[1] for r in proj_rows}
    members = {str(r[0]): r[1] for r in mem_rows}
    return projects, members


# ─── workspace lookup helper ─────────────────────────────────────────────────

async def _get_owned(db: AsyncSession, tenant_uuid: _uuid.UUID, workspace_id: str) -> Workspace:
    try:
        wid = _uuid.UUID(workspace_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="workspace_id must be a UUID")
    ws = (
        await db.execute(
            select(Workspace).where(
                Workspace.id == wid, Workspace.organization_id == tenant_uuid
            )
        )
    ).scalar_one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ws


# ─── role validation helper ───────────────────────────────────────────────────

async def _validate_role(db: AsyncSession, role_name: str) -> None:
    """Raise 422 if role_name is not in the global roles catalog."""
    role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()
    if role is None:
        raise HTTPException(
            status_code=422,
            detail=f"Role '{role_name}' does not exist. Valid roles: admin, product_manager, tech_lead, qa_lead, sre_lead, developer.",
        )


# ─── user resolution helper ───────────────────────────────────────────────────

async def _resolve_user(db: AsyncSession, user_input: str) -> User:
    """Resolve a user by auth-sub ID or email. Global users table (non-RLS)."""
    user = (await db.execute(select(User).where(User.id == user_input))).scalar_one_or_none()
    if user is None and "@" in user_input:
        user = (await db.execute(select(User).where(User.email == user_input))).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=404,
            detail=f"User '{user_input}' not found. Provide their auth sub (e.g. auth0|abc123) or registered email.",
        )
    return user


# ─── Workspace CRUD ───────────────────────────────────────────────────────────

@workspaces_router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(request: Request, db: AsyncSession = Depends(get_db_session)):
    tenant_id = _tenant_id(request)
    tenant_uuid = _uuid.UUID(tenant_id)

    # Org admins + workspace managers see all workspaces; everyone else sees only
    # workspaces they have a UserWorkspaceRole entry for.
    if _is_admin_or_manager(request):
        rows = (
            await db.execute(
                select(Workspace)
                .where(Workspace.organization_id == tenant_uuid)
                .order_by(Workspace.display_name)
            )
        ).scalars().all()
    else:
        user_id = getattr(request.state, "user_id", None) or ""
        member_ws_ids = select(UserWorkspaceRole.workspace_id).where(
            UserWorkspaceRole.user_id == user_id,
            UserWorkspaceRole.tenant_id == tenant_uuid,
        )
        rows = (
            await db.execute(
                select(Workspace)
                .where(
                    Workspace.organization_id == tenant_uuid,
                    Workspace.id.in_(member_ws_ids),
                )
                .order_by(Workspace.display_name)
            )
        ).scalars().all()

    projects, members = await _counts(db, tenant_uuid)
    spend = await _workspace_spend_map(db, tenant_uuid) if _can_view_cost(request) else {}
    return [
        _to_out(w, members.get(str(w.id), 0), projects.get(str(w.id), 0), spend.get(str(w.id), 0.0))
        for w in rows
    ]


@workspaces_router.post(
    "",
    response_model=WorkspaceOut,
    status_code=201,
    dependencies=[Depends(require_permission("workspace:manage"))],
)
async def create_workspace(
    body: WorkspaceCreateIn, request: Request, db: AsyncSession = Depends(get_db_session)
):
    tenant_id = _tenant_id(request)
    tenant_uuid = _uuid.UUID(tenant_id)
    name = body.displayName.strip()

    base = _slugify(name)
    existing = {
        s for (s,) in (
            await db.execute(
                select(Workspace.slug).where(Workspace.organization_id == tenant_uuid)
            )
        ).all()
    }
    slug = base
    i = 2
    while slug in existing:
        slug = f"{base}-{i}"
        i += 1

    # Default budget + hierarchical guard: a new workspace must fit under the org's
    # remaining budget, else 409 "Budget low" (blocks creation until the org cap is raised).
    from config.env import DEFAULT_WORKSPACE_BUDGET_USD  # noqa: PLC0415
    from shared.services.budget_alloc import assert_workspace_fits  # noqa: PLC0415
    ws_budget = body.monthlyBudgetUsd if body.monthlyBudgetUsd is not None else DEFAULT_WORKSPACE_BUDGET_USD
    await assert_workspace_fits(db, tenant_id, ws_budget, on_create=True)

    ws = Workspace(
        organization_id=tenant_uuid,
        slug=slug,
        display_name=name,
        business_unit=body.businessUnit,
        cost_center=body.costCenter,
        data_classification=body.dataClassification or "internal",
        status="active",
        monthly_budget_usd=ws_budget,
    )
    db.add(ws)
    await db.flush()

    # Auto-add the creator as admin member of the new workspace.
    creator_id = getattr(request.state, "user_id", None) or ""
    if creator_id:
        db.add(
            UserWorkspaceRole(
                user_id=creator_id,
                workspace_id=ws.id,
                role_name="admin",
                tenant_id=tenant_uuid,
            )
        )

    await db.commit()
    return _to_out(ws, 1 if creator_id else 0, 0)


@workspaces_router.get("/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(workspace_id: str, request: Request, db: AsyncSession = Depends(get_db_session)):
    tenant_uuid = _uuid.UUID(_tenant_id(request))
    ws = await _get_owned(db, tenant_uuid, workspace_id)
    projects, members = await _counts(db, tenant_uuid)
    from shared.services.budget_store import read_scope_spend  # noqa: PLC0415
    spend = await read_scope_spend(str(tenant_uuid), "workspace", str(ws.id)) if _can_view_cost(request) else 0.0
    return _to_out(ws, members.get(str(ws.id), 0), projects.get(str(ws.id), 0), spend)


@workspaces_router.patch(
    "/{workspace_id}",
    response_model=WorkspaceOut,
    dependencies=[Depends(require_permission("workspace:manage"))],
)
async def update_workspace(
    workspace_id: str, body: WorkspacePatchIn, request: Request, db: AsyncSession = Depends(get_db_session)
):
    tenant_uuid = _uuid.UUID(_tenant_id(request))
    ws = await _get_owned(db, tenant_uuid, workspace_id)
    if body.displayName is not None:
        ws.display_name = body.displayName.strip()
    if body.businessUnit is not None:
        ws.business_unit = body.businessUnit
    if body.costCenter is not None:
        ws.cost_center = body.costCenter
    if body.dataClassification is not None:
        ws.data_classification = body.dataClassification
    if body.status is not None:
        ws.status = body.status
    if body.monthlyBudgetUsd is not None:
        # 0 clears the cap (inherit org / unlimited); any positive value sets it.
        ws.monthly_budget_usd = body.monthlyBudgetUsd or None
    await db.commit()
    # Budgets are cached briefly for enforcement — invalidate so the edit applies now.
    from shared.services.budget_guard import clear_budget_cache  # noqa: PLC0415
    clear_budget_cache()
    from shared.services.budget_store import read_scope_spend  # noqa: PLC0415
    spend = await read_scope_spend(str(tenant_uuid), "workspace", str(ws.id)) if _can_view_cost(request) else 0.0
    projects, members = await _counts(db, tenant_uuid)
    return _to_out(ws, members.get(str(ws.id), 0), projects.get(str(ws.id), 0), spend)


@workspaces_router.post(
    "/{workspace_id}/archive",
    response_model=WorkspaceOut,
    dependencies=[Depends(require_permission("workspace:manage"))],
)
async def archive_workspace(
    workspace_id: str, request: Request, db: AsyncSession = Depends(get_db_session)
):
    tenant_uuid = _uuid.UUID(_tenant_id(request))
    ws = await _get_owned(db, tenant_uuid, workspace_id)
    ws.status = "archived"
    await db.commit()
    projects, members = await _counts(db, tenant_uuid)
    return _to_out(ws, members.get(str(ws.id), 0), projects.get(str(ws.id), 0))


# ─── Member management ────────────────────────────────────────────────────────

@workspaces_router.get("/{workspace_id}/members", response_model=list[WorkspaceMemberOut])
async def list_workspace_members(
    workspace_id: str, request: Request, db: AsyncSession = Depends(get_db_session)
):
    tenant_uuid = _uuid.UUID(_tenant_id(request))
    ws = await _get_owned(db, tenant_uuid, workspace_id)
    wid = ws.id

    rows = (
        await db.execute(
            select(UserWorkspaceRole, User)
            .outerjoin(User, User.id == UserWorkspaceRole.user_id)
            .where(UserWorkspaceRole.workspace_id == wid)
            .order_by(UserWorkspaceRole.created_at)
        )
    ).all()

    result = []
    for uwr, user in rows:
        email = user.email if user else None
        display_name = None
        initials = _initials(None, email, uwr.user_id)
        result.append(
            WorkspaceMemberOut(
                userId=uwr.user_id,
                email=email,
                displayName=display_name,
                initials=initials,
                roleName=uwr.role_name or "",
                joinedAt=uwr.created_at.isoformat() if uwr.created_at else "",
            )
        )
    return result


@workspaces_router.post(
    "/{workspace_id}/members",
    response_model=WorkspaceMemberOut,
    status_code=201,
    dependencies=[Depends(require_permission("workspace:manage"))],
)
async def add_workspace_member(
    workspace_id: str, body: AddMemberIn, request: Request, db: AsyncSession = Depends(get_db_session)
):
    tenant_uuid = _uuid.UUID(_tenant_id(request))
    await _get_owned(db, tenant_uuid, workspace_id)
    wid = _uuid.UUID(workspace_id)

    await _validate_role(db, body.roleName)
    user = await _resolve_user(db, body.userId)

    # Idempotency guard — prevent duplicate membership rows.
    existing = (
        await db.execute(
            select(UserWorkspaceRole).where(
                UserWorkspaceRole.user_id == user.id,
                UserWorkspaceRole.workspace_id == wid,
                UserWorkspaceRole.role_name == body.roleName,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"User is already a member of this workspace with role '{body.roleName}'.",
        )

    member = UserWorkspaceRole(
        user_id=user.id,
        workspace_id=wid,
        role_name=body.roleName,
        tenant_id=tenant_uuid,
    )
    db.add(member)
    await db.commit()

    initials = _initials(None, user.email, user.id)
    return WorkspaceMemberOut(
        userId=user.id,
        email=user.email,
        displayName=None,
        initials=initials,
        roleName=body.roleName,
        joinedAt=member.created_at.isoformat() if member.created_at else "",
    )


@workspaces_router.patch(
    "/{workspace_id}/members/{user_id}",
    response_model=WorkspaceMemberOut,
    dependencies=[Depends(require_permission("workspace:manage"))],
)
async def update_workspace_member_role(
    workspace_id: str, user_id: str, body: UpdateMemberRoleIn,
    request: Request, db: AsyncSession = Depends(get_db_session)
):
    tenant_uuid = _uuid.UUID(_tenant_id(request))
    await _get_owned(db, tenant_uuid, workspace_id)
    wid = _uuid.UUID(workspace_id)

    await _validate_role(db, body.roleName)

    # Find ANY existing role entry for this user in this workspace, then update it.
    uwr = (
        await db.execute(
            select(UserWorkspaceRole).where(
                UserWorkspaceRole.user_id == user_id,
                UserWorkspaceRole.workspace_id == wid,
            )
        )
    ).scalar_one_or_none()
    if uwr is None:
        raise HTTPException(status_code=404, detail="Member not found in this workspace")

    uwr.role_name = body.roleName
    await db.commit()

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    email = user.email if user else None
    return WorkspaceMemberOut(
        userId=user_id,
        email=email,
        displayName=None,
        initials=_initials(None, email, user_id),
        roleName=body.roleName,
        joinedAt=uwr.created_at.isoformat() if uwr.created_at else "",
    )


@workspaces_router.delete(
    "/{workspace_id}/members/{user_id}",
    status_code=204,
    dependencies=[Depends(require_permission("workspace:manage"))],
)
async def remove_workspace_member(
    workspace_id: str, user_id: str, request: Request, db: AsyncSession = Depends(get_db_session)
):
    tenant_uuid = _uuid.UUID(_tenant_id(request))
    await _get_owned(db, tenant_uuid, workspace_id)
    wid = _uuid.UUID(workspace_id)

    result = await db.execute(
        delete(UserWorkspaceRole).where(
            UserWorkspaceRole.user_id == user_id,
            UserWorkspaceRole.workspace_id == wid,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Member not found in this workspace")
    await db.commit()
