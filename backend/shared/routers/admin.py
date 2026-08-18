"""Admin RBAC management API — self-serve role assignment (the D-09 deferred feature).

Endpoints let a tenant admin list workspaces/roles/people and assign or revoke roles
per workspace. Every route is gated by member:manage via require_permission (Phase 6),
so delivery_lead (who holds member:manage per the RBAC matrix) can reach the members
surface alongside org_admin. admin:* still passes because has_permission honours the
wildcard. Cross-tenant access is structurally blocked by FORCE RLS on role_bindings.

Why member:manage and not admin:* (Phase 6 change):
  The RBAC matrix assigns this surface to both org_admin (admin:*) and delivery_lead
  (member:manage). The old _require_admin gate used admin:* only, blocking delivery_lead.
  require_permission("member:manage") fixes that; the admin:* wildcard still passes via
  has_permission so org_admin is unaffected.

  Known Phase 6 limitation: require_permission does resource-less workspace derivation
  (resolve_default_workspace), which would 409 for a multi-workspace tenant. This is correct
  under the current one-workspace-per-org model and is a documented accepted limitation
  (see plan 2026-06-12-phase6-org-rbac-pages.md). The dependency still carries the
  __rbac_require_permission__ sentinel so the D-05 boot scan recognises these routes
  as consciously protected.
"""
from __future__ import annotations

import logging
import re
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from typing import Optional

from shared.authz.dependency import require_permission
from shared.authz.grant import UnitAlreadyAdministeredError, grant_role, revoke_role
from shared.authz.grant_guard import assert_can_grant_role
from shared.authz.permissions import ALL_ROLES, _ROLE_PERMISSIONS, has_permission
from shared.authz.read_scope import assert_can_write_workspace
from shared.db import get_db_session
from shared.models.orm import AuditEvent, User, Workspace
from shared.routers._schemas import AuditEventOut, Paginated, Pagination
from shared.services.metrics import RBAC_DENIALS

logger = logging.getLogger(__name__)

# Human-facing labels/descriptions for the global role catalog (mirrors the frontend).
# Display metadata for GET /admin/roles. Labels mirror ROLE_META in
# frontend/lib/roles.ts so the same wording appears wherever a role is named.
_ROLE_META: dict[str, tuple[str, str]] = {
    "org_admin": ("Organization Admin", "Org-wide governance. Onboards business units and grants model and connector access."),
    "bu_admin": ("Business Unit Admin", "Runs one business unit: its projects, members, connections and budget."),
    "contributor": ("Contributor", "Onboarded into a unit, awaiting a real role from that unit's admin. Read-only."),
    "project_admin": ("Project Admin", "Owns a project, its members and its integrations. Approves where no specialist role exists."),
    "ba": ("Business Analyst", "Owns requirements and approves them."),
    "architect": ("Architect", "Owns design; approves design and development."),
    "developer": ("Developer", "Builds. Triggers runs, invokes agents and edits project skills."),
    "qa": ("QA Engineer", "Owns testing and approves it."),
    "security_engineer": ("Security Engineer", "Security review with audit, cost and trace visibility."),
    "devops_engineer": ("DevOps Engineer", "Owns deployment and approves it."),
    "data_engineer": ("Data Engineer", "Data pipelines and related delivery work."),
    "scrum_master": ("Scrum Master", "Process and visibility. Holds no approval authority."),
    "custom": ("Custom", "Tenant-defined role — permissions are attached separately."),
}


async def _require_admin(request: Request) -> None:
    """Router-level gate: deny unless the caller holds admin:* (or the wildcard).

    Reads permissions baked into the JWT at login (request.state.permissions, D-02).
    Carries the boot-scan sentinel so assert_all_routes_protected accepts these routes.
    """
    perms: list[str] = getattr(request.state, "permissions", []) or []
    if not has_permission(perms, "admin:*"):
        RBAC_DENIALS.labels(permission="admin:*", role="non_admin").inc()
        logger.warning(
            "RBAC denial (admin surface): user=%s tenant=%s",
            getattr(request.state, "user_id", "unknown"),
            getattr(request.state, "tenant_id", ""),
        )
        raise HTTPException(status_code=403, detail="Forbidden")


_require_admin.__rbac_require_permission__ = True  # D-05 boot-scan sentinel

# Phase 6: gate on member:manage (honors delivery_lead per matrix; admin:* still passes via
# has_permission wildcard). _require_admin kept for backward-compat with test_admin_access_api.py
# which imports it directly — it is no longer the active router gate.
admin_router = APIRouter(dependencies=[Depends(require_permission("member:manage"))])


# ───────────────────────── response / request models ─────────────────────────

class WorkspaceOut(BaseModel):
    id: str
    name: str


class RoleOut(BaseModel):
    name: str
    label: str
    description: str
    permissions: list[str]


class MemberOut(BaseModel):
    userId: str
    name: str | None
    email: str | None
    initials: str
    roles: list[str]


class AssignmentIn(BaseModel):
    user_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    role_name: str = Field(min_length=1)


def _initials(email: str | None, user_id: str) -> str:
    if email:
        local = email.split("@", 1)[0]
        parts = [p for p in re.split(r"[._\-+]+", local) if p]
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return (local[:2] or "?").upper()
    handle = user_id.rsplit("|", 1)[-1]
    return (handle[:2] or "?").upper()


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        # Defense-in-depth: the JWT middleware always sets this for protected routes.
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


# ───────────────────────── endpoints ─────────────────────────

@admin_router.get("/workspaces", response_model=list[WorkspaceOut])
async def list_workspaces(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> list[WorkspaceOut]:
    """Workspaces in the caller's tenant (tenant_id == organization_id)."""
    tenant_id = _tenant_id(request)
    rows = (
        await db.execute(
            select(Workspace)
            .where(Workspace.organization_id == _uuid.UUID(tenant_id))
            .order_by(Workspace.display_name)
        )
    ).scalars().all()
    return [WorkspaceOut(id=str(w.id), name=w.display_name) for w in rows]


@admin_router.get("/roles", response_model=list[RoleOut])
async def list_roles() -> list[RoleOut]:
    """Global role catalog with the permissions each role grants (D-03)."""
    out: list[RoleOut] = []
    for name in ALL_ROLES:
        label, description = _ROLE_META.get(
            name, (name.replace("_", " ").title(), "")
        )
        out.append(
            RoleOut(
                name=name,
                label=label,
                description=description,
                permissions=list(_ROLE_PERMISSIONS.get(name, [])),
            )
        )
    return out


@admin_router.get("/members", response_model=list[MemberOut])
async def list_members(
    request: Request,
    workspace_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> list[MemberOut]:
    """People (and their roles) in a workspace. RLS scopes rows to the tenant."""
    _tenant_id(request)  # enforce presence; RLS GUC already set by get_db_session
    try:
        wid = _uuid.UUID(workspace_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="workspace_id must be a UUID")

    rows = (
        await db.execute(
            text(
                "SELECT uwr.user_id, uwr.role_name, u.email "
                "FROM role_bindings uwr "
                "LEFT JOIN users u ON u.id = uwr.user_id "
                "WHERE uwr.scope_kind = 'business_unit' AND uwr.scope_id = :wid "
                "ORDER BY uwr.user_id, uwr.role_name"
            ),
            {"wid": wid},
        )
    ).all()

    members: dict[str, dict] = {}
    for user_id, role_name, email in rows:
        m = members.setdefault(
            user_id, {"userId": user_id, "email": email, "roles": []}
        )
        m["roles"].append(role_name)
        if email:
            m["email"] = email

    return [
        MemberOut(
            userId=m["userId"],
            name=None,
            email=m.get("email"),
            initials=_initials(m.get("email"), m["userId"]),
            roles=m["roles"],
        )
        for m in members.values()
    ]


class OkOut(BaseModel):
    ok: bool = True


@admin_router.post("/assignments", response_model=OkOut)
async def create_assignment(
    request: Request,
    body: AssignmentIn,
    db: AsyncSession = Depends(get_db_session),
) -> OkOut:
    """Assign a role to a user in a workspace (idempotent)."""
    tenant_id = _tenant_id(request)
    # The router gate (member:manage) says this caller manages members SOMEWHERE.
    # workspace_id arrives in the body and was never checked against that, so a
    # Business Unit Admin could grant any role in any sibling unit — the sharpest
    # escalation on this surface, since granting org_admin there escalates further.
    await assert_can_write_workspace(db, request, body.workspace_id)
    if body.role_name not in ALL_ROLES:
        raise HTTPException(
            status_code=422, detail=f"Unknown role '{body.role_name}'"
        )
    # ...and the other half of that escalation: the workspace check above says
    # WHERE this caller may grant, never WHAT. Without this, a Business Unit Admin
    # could grant org_admin inside their own unit and hold admin:* organization-wide
    # at the next login, because permissions resolve across scopes.
    await assert_can_grant_role(db, request, body.role_name)
    try:
        await grant_role(
            body.user_id,
            body.workspace_id,
            body.role_name,
            tenant_id=tenant_id,
            # The audit actor. Without it the trail records that someone gained a role
            # but not who gave it, which is the question actually asked afterwards.
            granted_by=getattr(request.state, "user_id", None),
        )
    except UnitAlreadyAdministeredError as exc:
        # 409: well-formed request, conflicting world. The message names the incumbent.
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return OkOut()


@admin_router.delete("/assignments", response_model=OkOut)
async def delete_assignment(
    request: Request,
    body: AssignmentIn,
    db: AsyncSession = Depends(get_db_session),
) -> OkOut:
    """Revoke a role from a user in a workspace (idempotent)."""
    tenant_id = _tenant_id(request)
    # Revocation needs the same guard as the grant: stripping a sibling unit's
    # admin of their role is a denial-of-service on that unit, not a lesser act.
    await assert_can_write_workspace(db, request, body.workspace_id)
    if body.role_name not in ALL_ROLES:
        raise HTTPException(
            status_code=422, detail=f"Unknown role '{body.role_name}'"
        )
    # Symmetric with the grant. Someone who may not confer a role has no standing
    # to take it away either — otherwise a Project Admin could strip an Organization
    # Admin's binding, which is the same authority exercised in the other direction.
    await assert_can_grant_role(db, request, body.role_name)
    try:
        await revoke_role(
            body.user_id,
            body.workspace_id,
            body.role_name,
            tenant_id=tenant_id,
            revoked_by=getattr(request.state, "user_id", None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return OkOut()


class OrgMemberOut(BaseModel):
    """All users in the org — no workspace context, just identity."""
    userId: str
    email: str | None
    initials: str


@admin_router.get("/org-members", response_model=list[OrgMemberOut])
async def list_org_members(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> list[OrgMemberOut]:
    """All active users in the caller's org (tenant_id-scoped, not workspace-scoped).

    Returns the full roster so workspace-scoped member management can show who is
    NOT yet a member of a specific workspace alongside those who are.
    The users table is global (no RLS), so we filter by tenant_id explicitly.
    """
    tenant_id = _tenant_id(request)
    rows = (
        await db.execute(
            select(User)
            .where(User.tenant_id == _uuid.UUID(tenant_id), User.active == True)  # noqa: E712
            .order_by(User.email)
        )
    ).scalars().all()
    return [
        OrgMemberOut(
            userId=u.id,
            email=u.email,
            initials=_initials(u.email, u.id),
        )
        for u in rows
    ]


class MemberCreateIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    role_name: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)


class MemberCreateOut(BaseModel):
    user_id: str
    email: str
    role_name: str


@admin_router.post("/members", response_model=MemberCreateOut, status_code=201)
async def create_member(
    request: Request,
    body: MemberCreateIn,
    db: AsyncSession = Depends(get_db_session),
) -> MemberCreateOut:
    """Create a user (email+password) and assign a role in a workspace."""
    import uuid as _uuid

    from shared.auth.passwords import hash_password
    from shared.authz.grant import grant_role
    from shared.db import get_db_session_superuser

    tenant_id = _tenant_id(request)
    # Reading a sibling unit's roster is a disclosure; creating a member inside one
    # is an escalation, so this guard matters more than any on the read paths.
    await assert_can_write_workspace(db, request, body.workspace_id)
    if body.role_name not in ALL_ROLES:
        raise HTTPException(status_code=422, detail=f"Unknown role '{body.role_name}'")
    # Creating the account and conferring the role happen together here, so this
    # route is a grant path like any other: without the rank check a Business Unit
    # Admin could mint a brand-new org_admin account inside their own unit.
    await assert_can_grant_role(db, request, body.role_name)
    email = body.email.strip().lower()
    user_id = str(_uuid.uuid4())
    pw_hash = hash_password(body.password)
    async with get_db_session_superuser() as s:
        dup = (await s.execute(
            text("SELECT 1 FROM users WHERE lower(email) = :e"), {"e": email}
        )).first()
        if dup:
            raise HTTPException(status_code=409, detail="A user with that email already exists")
        await s.execute(
            text(
                "INSERT INTO users (id, email, password_hash, tenant_id, active) "
                "VALUES (:id, :email, :ph, :tid, true)"
            ),
            {"id": user_id, "email": email, "ph": pw_hash, "tid": tenant_id},
        )
    try:
        await grant_role(
            user_id, body.workspace_id, body.role_name, tenant_id=tenant_id,
            # Was omitted, so the audit row recorded a role appearing with no author —
            # on the one route that creates the person and the binding in one act.
            granted_by=getattr(request.state, "user_id", None),
        )
    except UnitAlreadyAdministeredError as exc:
        # The account was already created above. Reporting the conflict rather than
        # rolling back is deliberate: the person exists and can be placed elsewhere or
        # given a different role, and deleting them would discard an email somebody
        # just typed.
        raise HTTPException(status_code=409, detail=str(exc))
    return MemberCreateOut(user_id=user_id, email=email, role_name=body.role_name)


# ─── Org-level audit log ──────────────────────────────────────────────────────

@admin_router.get("/audit", response_model=Paginated[AuditEventOut])
async def list_org_audit(
    request: Request,
    workspace_id: Optional[str] = None,
    actor: Optional[str] = None,
    action: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db_session),
) -> Paginated[AuditEventOut]:
    """Org-level audit log — all events across all workspaces.

    Accessible to org admins (admin:* via admin router gate). Supports an optional
    workspace_id filter so admins can drill into a single workspace's history or
    compare events across workspaces. No X-Workspace-Id header scoping — always
    returns the full tenant audit trail (or filtered subset when workspace_id provided).
    """
    tenant_id = _tenant_id(request)

    stmt = select(AuditEvent).where(AuditEvent.tenant_id == _uuid.UUID(tenant_id))
    if workspace_id:
        stmt = stmt.where(AuditEvent.payload["workspace_id"].astext == workspace_id)
    if actor:
        stmt = stmt.where(AuditEvent.actor_id == actor)
    if action:
        stmt = stmt.where(AuditEvent.event_type == action)

    total: int = (await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar_one()

    rows = (
        await db.execute(
            stmt.order_by(AuditEvent.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
        )
    ).scalars().all()

    return Paginated(
        items=[AuditEventOut.from_orm_audit(e) for e in rows],
        pagination=Pagination(page=page, pageSize=page_size, total=total),
    )
