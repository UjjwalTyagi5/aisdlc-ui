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
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import false as False_
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.dependency import require_permission
from shared.authz.grant import UnitAlreadyAdministeredError, grant_role, revoke_role
from shared.authz.grant_guard import assert_can_grant_role
from shared.authz.permissions import ROLE_TIER
from shared.authz.read_scope import allowed_workspace_ids, is_org_wide
from shared.db import get_db_session
from shared.models.orm import Project, Role, RoleBinding, UsageMonthly, User, Workspace
from shared.services.governance_requests import complete_role_assignment

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


def _sees_every_workspace(request: Request) -> bool:
    """True only for a caller whose reach is the whole organization.

    Was `admin:* or workspace:manage`, which conflated two different things. A
    Business Unit Admin holds workspace:manage FOR THE UNIT THEY RUN, so that test
    handed them the full list of sibling units — units they cannot open, whose
    names, budgets and headcounts are not theirs to see. The frontend used to hide
    those rows after the fact; with that filter gone, this is the only thing
    standing between a unit admin and their siblings.

    is_org_wide() is the same predicate the dashboard aggregates use, so both
    surfaces answer "the whole org, or just mine?" identically.
    """
    return is_org_wide(request)


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
    # Cosmetic-with-teeth: a custom role's display name lives in the roles store,
    # not in roleName (an opaque id like "role_3"), so without it the governance
    # request this endpoint closes (see complete_role_assignment below) would
    # record a raw id in its "Assigned ___." note instead of a name a human wrote.
    roleLabel: Optional[str] = None


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
            select(RoleBinding.scope_id, func.count(func.distinct(RoleBinding.user_id)))
            .where(RoleBinding.scope_kind == "business_unit")
            .group_by(RoleBinding.scope_id)
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

    # Org-wide callers see every unit; everyone else sees the units they may READ.
    #
    # THAT INCLUDES THE PARENT UNIT OF A PROJECT THEY ARE ON, which is what
    # `allowed_workspace_ids` adds over the binding query this used to run inline.
    # A Developer bound to one project held no business-unit binding, so this
    # returned an empty list to them — and with it their project's unit name, cap
    # and connectors, none of which they can work without. Worse, it made the unit
    # unpickable: the request form asks which unit an ask belongs to, and theirs
    # was not on the list, so a contributor could not raise a request at all.
    #
    # Reading the parent unit is not administering it: `administered_workspace_ids`
    # is the narrower question and every write still asks that one.
    allowed = await allowed_workspace_ids(db, request)
    query = select(Workspace).where(Workspace.organization_id == tenant_uuid)
    if allowed is not None:
        query = query.where(
            Workspace.id.in_([_uuid.UUID(w) for w in allowed]) if allowed else False_()
        )
    rows = (await db.execute(query.order_by(Workspace.display_name))).scalars().all()

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
    # Creating a business unit is an ORGANIZATION-wide act (PRD §15.2), so the
    # router-level workspace:manage gate is necessary but not sufficient: a unit's
    # own Admin holds workspace:manage for the unit they run, and passing only that
    # check would let them create siblings they have no authority over.
    #
    # This lived in the Next.js route handler until the fixtures were removed. It
    # belongs here — the BFF is the only caller today, but "the only caller today"
    # is not an authorization boundary.
    if not is_org_wide(request):
        raise HTTPException(
            status_code=403,
            detail="Only an Organization Admin can create a business unit",
        )

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
    from shared.services.budget_alloc import assert_workspace_fits  # noqa: PLC0415
    # NO DEFAULT. A business unit's budget is optional, and omitting it means the
    # unit has no cap — not that it silently acquires DEFAULT_WORKSPACE_BUDGET_USD,
    # which is a ceiling nobody chose and which then bounds every project under it.
    ws_budget = body.monthlyBudgetUsd if body.monthlyBudgetUsd else None
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

    # THE CREATOR IS NOT MADE THE UNIT'S ADMIN, and removing that was the point.
    #
    # This used to bind the creator as `bu_admin` here. It gave them nothing — only an
    # Organization Admin can reach this route (see the org-wide gate above) and org-wide
    # standing already reaches every unit without a per-unit binding. What it DID do was
    # occupy the unit's single admin slot: a unit has exactly one Business Unit Admin, so
    # the Org Admin who created it could never appoint a real one without first removing
    # themselves from a unit they were never meant to run.
    #
    # A new unit therefore starts with no admin, which is the honest state and the one the
    # two-step model describes: the Organization Admin creates the unit and then APPOINTS
    # its admin, deliberately, through onboarding.
    #
    # It also wrote `RoleBinding` directly through the ORM, so it skipped the tier check,
    # the single-admin check and the audit row that `shared/authz/grant.py` exists to
    # apply. That is the same class of bug as finding 7 in docs/rbac-audit-2026-08-17.md.
    await db.commit()
    return _to_out(ws, 0, 0)


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


class BudgetIncreaseIn(BaseModel):
    requestedAmountUsd: float = Field(gt=0)
    reason: Optional[str] = Field(default=None, max_length=2000)


@workspaces_router.post("/{workspace_id}/budget-increase-request", status_code=201)
async def request_budget_increase(
    workspace_id: str,
    request: Request,
    body: BudgetIncreaseIn,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Ask for more budget than you may set yourself.

    THE OTHER HALF OF THE BUDGET CASCADE (PRD §34.5). A unit's own Admin may set
    the FIRST cap directly — the Org Admin is allowed to create a unit without one
    and somebody has to fill in the blank — but changing a cap that already exists
    is a different act, with a prior figure someone agreed to, and it comes here.

    A DEDICATED ENDPOINT RATHER THAN `POST /governance-approvals`, and the reason is
    the amount. The generic create endpoint deliberately accepts no `payload` and no
    `target_ref`, because those are what the approval's SIDE EFFECT reads: a client
    that could set them could file an `agent_default_*` request pointing at any
    profile version and have approving it publish that version. So every request
    type carrying a consequence gets a typed filing point that fills those in from
    something the server checked. Here that is a positive number and a unit the
    caller can reach.

    The floor is `cost:view`, not `workspace:manage`: asking is not changing, and
    the person who can see a cap about to bind is the person who should be able to
    raise it.
    """
    tenant_id = _tenant_id(request)
    tenant_uuid = _uuid.UUID(tenant_id)
    # 404s a unit in another tenant, and one this caller cannot reach.
    ws = await _get_owned(db, tenant_uuid, workspace_id)

    from shared.authz.effective_role import (  # noqa: PLC0415 - avoids an import cycle
        actor_display_name,
        effective_platform_role,
    )
    from shared.services import governance_requests as governance_service  # noqa: PLC0415
    from shared.services.governance_requests import GovernanceError  # noqa: PLC0415

    amount = body.requestedAmountUsd
    current = float(ws.monthly_budget_usd) if ws.monthly_budget_usd is not None else None
    detail = (
        f"{ws.display_name} is asking to move its monthly cap "
        + (f"from {current:.0f} " if current is not None else "")
        + f"to {amount:.0f} USD."
        + (f" {body.reason}" if body.reason else "")
    )

    try:
        return await governance_service.create_request(
            db,
            tenant_id=tenant_id,
            initiator_id=getattr(request.state, "user_id", "") or "",
            initiator_name=await actor_display_name(db, request),
            initiator_role=await effective_platform_role(db, request),
            request_type="budget_increase",
            title=f"Budget increase: {ws.display_name} — {amount:.0f} USD/month",
            description=detail,
            workspace_id=str(ws.id),
            target_ref=str(ws.id),
            # The figure the approver will read and agree to. Read back at decision
            # time from HERE, never from the decision call — otherwise someone
            # approves one number and a different one is applied.
            payload={"requestedAmountUsd": amount, "previousAmountUsd": current},
            priority="high",
        )
    except GovernanceError as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"code": exc.code, "message": str(exc)}
        )


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
            select(RoleBinding, User)
            .outerjoin(User, User.id == RoleBinding.user_id)
            .where(RoleBinding.scope_kind == "business_unit", RoleBinding.scope_id == wid)
            .order_by(RoleBinding.created_at)
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
            select(RoleBinding).where(
                RoleBinding.user_id == user.id,
                RoleBinding.scope_kind == "business_unit",
                RoleBinding.scope_id == wid,
                RoleBinding.role_name == body.roleName,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"User is already a member of this workspace with role '{body.roleName}'.",
        )

    member = RoleBinding(
        user_id=user.id,
        scope_kind="business_unit",
        scope_id=wid,
        role_name=body.roleName,
        tier=ROLE_TIER.get(body.roleName),
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

    # THIS ENDPOINT USED TO ASSIGN `uwr.role_name` AND COMMIT, which bypassed every
    # rule `grant_role` exists to enforce — on the one screen where roles are actually
    # changed:
    #
    #   * NO RANK CHECK. The route gate is `workspace:manage`, which a Business Unit
    #     Admin holds, so this was a PATCH away from setting anybody — including
    #     themselves — to org_admin, and holding admin:* organization-wide at the next
    #     login. `assert_can_grant_role` is the guard for exactly that, and every other
    #     grant path calls it.
    #   * NO AUDIT. `record_rbac_change` never ran, so a role change left no trace at
    #     all. Ana was moved from Contributor to Project Admin and her history showed
    #     only the original onboarding grant — the change nobody could account for was
    #     the one somebody actually made.
    #   * NO TIER CONFLICT CHECK, so governance and delivery could be held in one
    #     scope, which is the self-approval the tiers exist to prevent.
    #   * NO ONE-ADMIN-PER-UNIT CHECK, so a second bu_admin could be installed past
    #     the rule that refuses it everywhere else.
    #   * A HANDLER-LEVEL `db.commit()`, which drops the transaction-scoped tenant GUC
    #     and makes every subsequent read in the request return empty.
    #
    # Routed through revoke + grant instead. That is also what produces a readable
    # history: two events naming the role that went and the role that came, rather
    # than one row quietly holding a different value than it did yesterday.
    await assert_can_grant_role(db, request, body.roleName)

    existing = (
        await db.execute(
            select(RoleBinding).where(
                RoleBinding.user_id == user_id,
                RoleBinding.scope_kind == "business_unit",
                RoleBinding.scope_id == wid,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        raise HTTPException(status_code=404, detail="Member not found in this workspace")

    previous_role = existing.role_name
    joined_at = existing.created_at
    actor = getattr(request.state, "user_id", None)

    if previous_role != body.roleName:
        # Granting the new role BEFORE revoking the old one would trip the
        # one-bu-admin and tier-conflict rules against the member's own outgoing
        # role. Revoke first, and let grant_role fail the change as a whole if the
        # new role is not allowed — a half-applied change would leave somebody with
        # no role at all.
        if previous_role:
            await revoke_role(
                user_id, wid, previous_role,
                tenant_id=str(tenant_uuid), scope_kind="business_unit",
                revoked_by=actor,
            )
        try:
            await grant_role(
                user_id, wid, body.roleName,
                tenant_id=str(tenant_uuid), scope_kind="business_unit",
                granted_by=actor,
            )
        except UnitAlreadyAdministeredError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        # Close the onboarding-raised role_assignment request this discharges, if
        # one is open — see onboarding.py: "it closes when a role is actually
        # assigned rather than by being approved." Best-effort; never raises, so a
        # governance-table hiccup can never undo the role change above.
        from shared.authz.effective_role import actor_display_name
        await complete_role_assignment(
            db,
            tenant_id=str(tenant_uuid),
            workspace_id=workspace_id,
            user_id=user_id,
            role_label=body.roleLabel or body.roleName,
            decided_by_id=actor,
            decided_by_name=await actor_display_name(db, request),
        )

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    email = user.email if user else None
    return WorkspaceMemberOut(
        userId=user_id,
        email=email,
        displayName=None,
        initials=_initials(None, email, user_id),
        roleName=body.roleName,
        joinedAt=joined_at.isoformat() if joined_at else "",
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
        delete(RoleBinding).where(
            RoleBinding.user_id == user_id,
            RoleBinding.scope_kind == "business_unit",
            RoleBinding.scope_id == wid,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Member not found in this workspace")
    await db.commit()
