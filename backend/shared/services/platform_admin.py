"""Platform-tier admin operations (Phase 5). All cross-tenant via the BYPASSRLS
superuser session — platform admins are tenant-less. Routers stay thin; the
read aggregation + writes live here (mirrors provisioning.py backing create-org).
"""
from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import text

from shared.auth.passwords import hash_password
from shared.db import get_db_session_superuser

logger = logging.getLogger(__name__)


class OrgNotFoundError(Exception):
    """Raised when an organization id does not exist."""


class AdminNotFoundError(Exception):
    """Raised when the email is not an org_admin of the given org."""


class PlatformUserExistsError(Exception):
    """Raised when a platform user with that email already exists."""


class PlatformUserNotFoundError(Exception):
    """Raised when a platform_users row for the given user_id does not exist."""


async def get_org_detail(org_id: str) -> dict:
    async with get_db_session_superuser() as s:
        org = (await s.execute(
            text("SELECT id, slug, display_name, suspended FROM organizations WHERE id = :id"),
            {"id": org_id},
        )).first()
        if org is None:
            raise OrgNotFoundError(org_id)

        workspaces = (await s.execute(
            text("SELECT id, slug, display_name FROM workspaces WHERE organization_id = :id "
                 "ORDER BY display_name"),
            {"id": org_id},
        )).fetchall()

        # org_admin holders in this tenant, joined to their email.
        admins = (await s.execute(
            text(
                "SELECT DISTINCT u.id, u.email FROM user_workspace_roles uwr "
                "JOIN users u ON u.id = uwr.user_id "
                "WHERE uwr.tenant_id = :id AND uwr.role_name = 'org_admin' "
                "ORDER BY u.email"
            ),
            {"id": org_id},
        )).fetchall()

        # All members of this org with their assigned roles (default role_name or
        # custom role name), aggregated per user.
        members = (await s.execute(
            text(
                "SELECT u.id, u.email, "
                "  array_agg(DISTINCT COALESCE(uwr.role_name, cr.name)) AS roles "
                "FROM user_workspace_roles uwr "
                "JOIN users u ON u.id = uwr.user_id "
                "LEFT JOIN custom_roles cr ON cr.id = uwr.custom_role_id "
                "WHERE uwr.tenant_id = :id "
                "GROUP BY u.id, u.email ORDER BY u.email"
            ),
            {"id": org_id},
        )).fetchall()

        member_count = (await s.execute(
            text("SELECT COUNT(DISTINCT user_id) FROM user_workspace_roles WHERE tenant_id = :id"),
            {"id": org_id},
        )).scalar() or 0

        run_count = (await s.execute(
            text("SELECT COUNT(*) FROM runs WHERE tenant_id = :id"), {"id": org_id},
        )).scalar() or 0

        total_cost = (await s.execute(
            text("SELECT COALESCE(SUM(cost_usd), 0) FROM agent_call_logs WHERE tenant_id = :id"),
            {"id": org_id},
        )).scalar() or 0

    return {
        "org_id": str(org.id),
        "slug": org.slug,
        "display_name": org.display_name,
        "suspended": bool(org.suspended),
        "workspaces": [{"id": str(w.id), "slug": w.slug, "display_name": w.display_name}
                       for w in workspaces],
        "admins": [{"user_id": str(a.id), "email": a.email} for a in admins],
        "members": [
            {"user_id": str(m.id), "email": m.email,
             "roles": [r for r in (m.roles or []) if r]}
            for m in members
        ],
        "member_count": int(member_count),
        "run_count": int(run_count),
        "total_cost_usd": float(total_cost),
    }


async def set_org_suspended(org_id: str, suspended: bool) -> None:
    async with get_db_session_superuser() as s:
        result = await s.execute(
            text("UPDATE organizations SET suspended = :v WHERE id = :id"),
            {"v": suspended, "id": org_id},
        )
        if result.rowcount == 0:
            raise OrgNotFoundError(org_id)
    logger.info("org %s suspended=%s", org_id, suspended)


async def reset_org_admin_password(org_id: str, email: str, new_password: str) -> None:
    email = (email or "").strip().lower()
    if len(new_password or "") < 8:
        raise ValueError("password must be at least 8 characters")
    async with get_db_session_superuser() as s:
        row = (await s.execute(
            text(
                "SELECT u.id FROM users u "
                "JOIN user_workspace_roles uwr ON uwr.user_id = u.id "
                "WHERE uwr.tenant_id = :org AND uwr.role_name = 'org_admin' "
                "AND lower(u.email) = :email LIMIT 1"
            ),
            {"org": org_id, "email": email},
        )).first()
        if row is None:
            raise AdminNotFoundError(email)
        await s.execute(
            text("UPDATE users SET password_hash = :ph WHERE id = :id"),
            {"ph": hash_password(new_password), "id": row.id},
        )
    logger.info("reset org_admin password org=%s email=%s", org_id, email)


_PLATFORM_ROLES = {"platform_admin", "platform_support"}


async def list_platform_users() -> list[dict]:
    async with get_db_session_superuser() as s:
        rows = (await s.execute(
            text("SELECT user_id, email, platform_role, active, created_at "
                 "FROM platform_users ORDER BY email")
        )).fetchall()
    return [
        {"user_id": str(r.user_id), "email": r.email, "platform_role": r.platform_role,
         "active": bool(r.active), "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]


async def create_platform_user(email: str, password: str, role: str) -> dict:
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("email is required")
    if len(password or "") < 8:
        raise ValueError("password must be at least 8 characters")
    if role not in _PLATFORM_ROLES:
        raise ValueError(f"role must be one of {sorted(_PLATFORM_ROLES)}")

    user_id = str(_uuid.uuid4())
    pw_hash = hash_password(password)
    async with get_db_session_superuser() as s:
        exists = (await s.execute(
            text("SELECT 1 FROM platform_users WHERE email = :e"), {"e": email},
        )).first()
        if exists:
            raise PlatformUserExistsError(email)
        await s.execute(
            text("INSERT INTO users (id, email, password_hash, tenant_id, active) "
                 "VALUES (:id, :email, :ph, NULL, true)"),
            {"id": user_id, "email": email, "ph": pw_hash},
        )
        await s.execute(
            text("INSERT INTO platform_users (user_id, email, platform_role, active) "
                 "VALUES (:id, :email, :role, true)"),
            {"id": user_id, "email": email, "role": role},
        )
    logger.info("created platform user email=%s role=%s", email, role)
    return {"user_id": user_id, "email": email, "platform_role": role, "active": True}


async def set_platform_user_active(user_id: str, active: bool) -> None:
    async with get_db_session_superuser() as s:
        result = await s.execute(
            text("UPDATE platform_users SET active = :a WHERE user_id = :id"),
            {"a": active, "id": user_id},
        )
        if result.rowcount == 0:
            raise PlatformUserNotFoundError(user_id)
        await s.execute(
            text("UPDATE users SET active = :a WHERE id = :id"),
            {"a": active, "id": user_id},
        )
    logger.info("platform user %s active=%s", user_id, active)
