"""DB-backed permission resolver for login-time JWT claim population.

resolve_permissions_for_user is called once at login by the token-issuance path;
the result is baked into the JWT (REQ-M7-12, D-02).  Enforcement reads the claim
from request.state — no per-request DB hit in the common path.

resolve_primary_role_for_user is advisory (used for metric labels only — Pitfall 7 / A2);
it is never the authoritative authz check.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from shared.db import get_db_session_for_tenant
from shared.models.orm import CustomRolePermission, RolePermission, UserWorkspaceRole

logger = logging.getLogger(__name__)


class PermissionResolutionError(Exception):
    """Raised when permission resolution fails for an infrastructure reason.

    Distinguishes "this user legitimately has no role assignment" (→ []) from
    "the resolver could not reach the DB" (→ raise) so the caller can fail the
    request with 503 instead of silently downgrading an admin to zero permissions
    on a transient DB blip (WR-05).
    """


async def resolve_permissions_for_user(user_id: str, tenant_id: str) -> list[str]:
    """Return the sorted, deduplicated effective permission set for a user.

    Queries user_workspace_roles JOIN role_permissions under the tenant GUC so RLS
    restricts the result to the caller's tenant automatically (D-03).

    Returns [] immediately when tenant_id is falsy — fail-closed (D-02).  Returns []
    for a user with no role assignment (genuine empty set).  Raises
    PermissionResolutionError on an infrastructure failure (DB outage) so the caller
    rejects the request rather than proceeding with empty permissions (WR-05).
    """
    if not tenant_id:
        # No tenant context — cannot resolve tenant-scoped assignments.
        return []

    try:
        async with get_db_session_for_tenant(tenant_id) as session:
            default_stmt = (
                select(RolePermission.permission_name)
                .join(
                    UserWorkspaceRole,
                    UserWorkspaceRole.role_name == RolePermission.role_name,
                )
                .where(UserWorkspaceRole.user_id == user_id)
            )
            custom_stmt = (
                select(CustomRolePermission.permission_name)
                .join(
                    UserWorkspaceRole,
                    UserWorkspaceRole.custom_role_id == CustomRolePermission.custom_role_id,
                )
                .where(UserWorkspaceRole.user_id == user_id)
            )
            default_rows = (await session.execute(default_stmt)).scalars().all()
            custom_rows = (await session.execute(custom_stmt)).scalars().all()
            return sorted(set(default_rows) | set(custom_rows))
    except Exception as exc:
        logger.exception(
            "resolve_permissions_for_user failed for user=%s tenant=%s — raising "
            "PermissionResolutionError (fail-closed, not empty-list)",
            user_id,
            tenant_id,
        )
        raise PermissionResolutionError(str(exc)) from exc


async def resolve_primary_role_for_user(user_id: str, tenant_id: str) -> str:
    """Return a single low-cardinality role label for metric purposes only.

    Advisory — NOT authoritative for access control (Pitfall 7 / A2).  Returns
    "unknown" when no assignment exists or tenant_id is falsy.
    """
    if not tenant_id:
        return "unknown"

    try:
        async with get_db_session_for_tenant(tenant_id) as session:
            stmt = (
                select(UserWorkspaceRole.role_name)
                .where(UserWorkspaceRole.user_id == user_id)
                .limit(1)
            )
            result = await session.execute(stmt)
            role = result.scalar_one_or_none()
            return role or "unknown"
    except Exception:
        return "unknown"
