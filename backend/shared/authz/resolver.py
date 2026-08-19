"""DB-backed permission resolver for login-time JWT claim population.

resolve_permissions_for_user is called once at login by the token-issuance path;
the result is baked into the JWT (REQ-M7-12, D-02).  Enforcement reads the claim
from request.state — no per-request DB hit in the common path.

resolve_primary_role_for_user is advisory (used for metric labels only — Pitfall 7 / A2);
it is never the authoritative authz check.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import or_, select

from shared.authz.role_permissions import effective_for_roles
from shared.db import get_db_session_for_tenant
from shared.models.orm import CustomRolePermission, RolePermission, RoleBinding  # noqa: F401

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

    Queries role_bindings JOIN role_permissions under the tenant GUC so RLS
    restricts the result to the caller's tenant automatically (D-03).

    Unions across every binding the user holds regardless of scope_kind — a person
    with org-level, business-unit-level and project-level bindings gets the union of
    all three. WHICH scope each permission applies to is answered separately by
    shared/authz/scope.py; this function only answers WHAT the user may do.

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
            # Built-in roles no longer join role_permissions directly. That table is
            # the SHIPPED DEFAULT; what a role grants in THIS organization may have
            # been retuned from the Roles page, and `effective_for_roles` is the one
            # place the two are merged. Joining the default here would let a token
            # minted at login disagree with the scoped checks in can_perform, which
            # reads through the same helper.
            now = datetime.now(tz=timezone.utc)
            # ONLY LIVE BINDINGS. This query used to filter on `user_id` alone, so a
            # deactivated binding and an EXPIRED one both kept granting until the token
            # lapsed — and since this is the claim every `require_permission` reads,
            # that was the whole product. `can_perform` filtered both correctly, which
            # is worse than neither doing it: the two permission readers disagreed about
            # the same binding, so a temporary elevation was refused on a project page
            # and honoured everywhere else.
            #
            # `status = 'active'` (not `<> 'deactivated'`) matches `can_perform`
            # exactly. Nothing writes 'invited' today, so the two are equivalent in
            # practice; picking the strict one means the day something does write it,
            # an unaccepted invitation grants nothing rather than everything.
            live = (
                RoleBinding.user_id == user_id,
                RoleBinding.status == "active",
                or_(RoleBinding.expires_at.is_(None), RoleBinding.expires_at > now),
            )

            role_names = list(
                (
                    await session.execute(
                        select(RoleBinding.role_name).where(
                            *live,
                            RoleBinding.role_name.isnot(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            builtin = await effective_for_roles(session, role_names, tenant_id)

            # Custom roles carry their own permission rows and are not overridable —
            # editing one IS editing its permissions, so there is no default to
            # diverge from. Same liveness filter: a custom-role binding expires exactly
            # like a built-in one, and omitting it here would leave a hole shaped like
            # the one above but harder to spot.
            custom_stmt = (
                select(CustomRolePermission.permission_name)
                .join(
                    RoleBinding,
                    RoleBinding.custom_role_id == CustomRolePermission.custom_role_id,
                )
                .where(*live)
            )
            custom_rows = (await session.execute(custom_stmt)).scalars().all()
            return sorted(builtin | set(custom_rows))
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
                select(RoleBinding.role_name)
                .where(RoleBinding.user_id == user_id)
                .limit(1)
            )
            result = await session.execute(stmt)
            role = result.scalar_one_or_none()
            return role or "unknown"
    except Exception:
        return "unknown"
