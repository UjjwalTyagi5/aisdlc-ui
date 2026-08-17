"""Idempotent role-assignment helper + operator CLI (D-09).

grant_role(user_id, scope_id, role_name, *, tenant_id, scope_kind, tier):
  Writes a RoleBinding row via get_db_session_for_tenant (sets the RLS GUC).
  A plain/superuser session is BLOCKED by FORCE RLS on role_bindings (Pitfall 5).
  ON CONFLICT (user_id, scope_kind, scope_id, role_name) DO NOTHING — idempotent re-runs.
  Also upserts the User row if absent (D-08: global identity, no RLS, SCIM-forward).

This is the ONLY write path into role_bindings.  It is operator-only (no HTTP route,
no UI) and must never be imported from request-handling code.

CLI usage (operator-only):
  python -m shared.authz.grant \\
    --user-id <sub> --scope-id <uuid> --role <role_name> --tenant-id <uuid> \\
    [--scope-kind organization|business_unit|project]

Structurally incapable of crossing tenants: write goes through get_db_session_for_tenant
which sets app.current_tenant_id; FORCE RLS on role_bindings enforces the policy.
"""
from __future__ import annotations

import asyncio
import logging
import uuid as _uuid
from datetime import datetime, timezone

from sqlalchemy import text

from shared.authz.audit import (
    RBAC_CUSTOM_ROLE_GRANTED,
    RBAC_ROLE_GRANTED,
    RBAC_ROLE_REVOKED,
    record_rbac_change,
)
from shared.authz.permissions import ALL_ROLES, ROLE_TIER
from shared.db import get_db_session_for_tenant

logger = logging.getLogger(__name__)

VALID_SCOPE_KINDS = ("organization", "business_unit", "project", "workstream")


class TierConflictError(Exception):
    """Raised when a grant would leave a user holding both tiers within one scope.

    Governance approves work; delivery does the work. Holding both in the SAME scope
    means approving your own output, so it is rejected. Holding governance in one scope
    and delivery in another is legitimate and explicitly allowed — which is why this
    cannot be a table constraint and has to be checked here, per scope.
    """


async def _assert_no_tier_conflict(session, user_id: str, scope_kind: str, scope_id: str, tier: str | None) -> None:
    """Reject a grant that would mix governance and delivery within one scope."""
    if tier is None:
        return
    rows = await session.execute(
        text(
            "SELECT DISTINCT tier FROM role_bindings "
            "WHERE user_id = :user_id AND scope_kind = :scope_kind AND scope_id = :scope_id "
            "  AND tier IS NOT NULL AND status <> 'deactivated'"
        ),
        {"user_id": user_id, "scope_kind": scope_kind, "scope_id": scope_id},
    )
    existing = {r[0] for r in rows}
    conflicting = existing - {tier}
    if conflicting:
        raise TierConflictError(
            f"user {user_id} already holds tier {sorted(conflicting)} in "
            f"{scope_kind} {scope_id}; granting a '{tier}' role there would let them "
            "approve their own work. Grant it at a different scope instead."
        )


async def grant_role(
    user_id: str,
    scope_id: _uuid.UUID | str,
    role_name: str,
    *,
    tenant_id: str,
    scope_kind: str = "business_unit",
    tier: str | None = None,
    expires_at: datetime | None = None,
    granted_by: str | None = None,
) -> None:
    """Idempotently assign role_name to user_id at (scope_kind, scope_id) under tenant_id.

    Args:
        user_id:    JWT sub / external identity string — must be non-empty.
        scope_id:   UUID of the organization, business unit or project to bind at.
        role_name:  Must be one of ALL_ROLES from shared.authz.permissions.
        tenant_id:  The org/tenant UUID string — REQUIRED and NEVER empty.
                    An empty tenant would write under an unset GUC, which FORCE RLS
                    would reject anyway, but we fail-close explicitly (mirrors the
                    resolver's fail-closed contract).
        scope_kind: One of VALID_SCOPE_KINDS. Defaults to "business_unit" because that
                    is what every pre-existing caller meant when this took a workspace_id.
        tier:       "governance" | "delivery" | None. Defaults to the role's own tier
                    from ROLE_TIER when not given.
        expires_at: When set, the assignment stops granting anything after this
                    instant — enforced by the clock in can_perform, not by a sweep
                    job, so a temporary elevation ends whether or not anything
                    remembers to revoke it. None = permanent.
        granted_by: Who issued this. Recorded because a time-bound elevation is
                    exactly the grant someone asks about afterwards.

    Raises TierConflictError if the grant would leave the user holding both governance
    and delivery within this one scope — see _assert_no_tier_conflict.

    Why the tenant-scoped session and not a superuser/bypass-RLS session:
      FORCE RLS on role_bindings means even a privileged connection would need
      the GUC set to satisfy the WITH CHECK policy (Pitfall 5).  The tenant session
      is the ONLY path that sets app.current_tenant_id and can satisfy the policy.
      Structurally incapable of crossing tenants — the GUC locks the write to tenant_id.
    """
    if not tenant_id:
        raise ValueError(
            "tenant_id is required for grant_role — an empty tenant cannot reach the write "
            "(fail-closed, mirrors resolve_permissions_for_user contract, T-7.2-20)"
        )

    if role_name not in ALL_ROLES:
        raise ValueError(
            f"Unknown role '{role_name}'. Valid roles: {sorted(ALL_ROLES)}"
        )

    if scope_kind not in VALID_SCOPE_KINDS:
        raise ValueError(
            f"Unknown scope_kind '{scope_kind}'. Valid: {list(VALID_SCOPE_KINDS)}"
        )

    scope_uuid = _uuid.UUID(str(scope_id))
    tenant_uuid = _uuid.UUID(str(tenant_id))
    new_binding_id = _uuid.uuid4()
    now = datetime.now(tz=timezone.utc)
    effective_tier = tier if tier is not None else ROLE_TIER.get(role_name)

    async with get_db_session_for_tenant(tenant_id) as session:
        await _assert_no_tier_conflict(
            session, user_id, scope_kind, str(scope_uuid), effective_tier
        )
        # (a) Upsert the User row if absent — User is GLOBAL (non-RLS, D-08).
        # Keeps email/external_id NULL — SCIM will populate them in 7.4.
        await session.execute(
            text(
                "INSERT INTO users (id, tenant_id, created_at) "
                "VALUES (:id, :tenant_id, :created_at) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": user_id,
                "tenant_id": str(tenant_uuid),
                "created_at": now,
            },
        )

        # (b) Assign the role — ON CONFLICT on (user_id, scope_kind, scope_id, role_name).
        # ON CONFLICT (col list) is portable across constraint naming conventions.
        await session.execute(
            text(
                "INSERT INTO role_bindings "
                "  (id, user_id, scope_kind, scope_id, role_name, tier, status, tenant_id, "
                "   created_at, expires_at, granted_by) "
                "VALUES "
                "  (:id, :user_id, :scope_kind, :scope_id, :role_name, :tier, 'active', :tenant_id, "
                "   :created_at, :expires_at, :granted_by) "
                # DO UPDATE, not DO NOTHING, on the expiry columns only: re-granting an
                # existing role with a new expiry is how an elevation is extended, and
                # DO NOTHING would silently ignore the extension while reporting success.
                "ON CONFLICT (user_id, scope_kind, scope_id, role_name) DO UPDATE SET "
                "  expires_at = EXCLUDED.expires_at, "
                "  granted_by = COALESCE(EXCLUDED.granted_by, role_bindings.granted_by)"
            ),
            {
                "id": str(new_binding_id),
                "user_id": user_id,
                "scope_kind": scope_kind,
                "scope_id": str(scope_uuid),
                "role_name": role_name,
                "tier": effective_tier,
                "tenant_id": str(tenant_uuid),
                "created_at": now,
                "expires_at": expires_at,
                "granted_by": granted_by,
            },
        )

        # Audited inside the same transaction as the write above, so the binding and
        # the record of it commit together — an audit row describing a grant that was
        # rolled back is worse than no row at all.
        await record_rbac_change(
            session,
            tenant_id=str(tenant_uuid),
            actor_id=granted_by,
            event_type=RBAC_ROLE_GRANTED,
            subject_id=user_id,
            scope_kind=scope_kind,
            scope_id=str(scope_uuid),
            role=role_name,
            extra={
                "tier": effective_tier,
                "expires_at": expires_at.isoformat() if expires_at else None,
            },
        )

    logger.info(
        "grant_role: user=%s scope=%s:%s role=%s tier=%s tenant=%s (idempotent)",
        user_id,
        scope_kind,
        scope_uuid,
        role_name,
        effective_tier,
        tenant_uuid,
    )


async def revoke_role(
    user_id: str,
    scope_id: _uuid.UUID | str,
    role_name: str,
    *,
    tenant_id: str,
    scope_kind: str = "business_unit",
    revoked_by: str | None = None,
) -> None:
    """Remove role_name from user_id at (scope_kind, scope_id) under tenant_id (idempotent).

    Mirror of grant_role: the DELETE runs through get_db_session_for_tenant so the
    RLS GUC (app.current_tenant_id) is set — FORCE RLS on role_bindings makes
    the delete structurally incapable of crossing tenants (a row in another tenant is
    invisible to the policy and therefore cannot be deleted). Deleting a non-existent
    assignment is a no-op (idempotent).
    """
    if not tenant_id:
        raise ValueError(
            "tenant_id is required for revoke_role — an empty tenant cannot reach the "
            "write (fail-closed, mirrors grant_role contract)"
        )

    if role_name not in ALL_ROLES:
        raise ValueError(
            f"Unknown role '{role_name}'. Valid roles: {sorted(ALL_ROLES)}"
        )

    if scope_kind not in VALID_SCOPE_KINDS:
        raise ValueError(
            f"Unknown scope_kind '{scope_kind}'. Valid: {list(VALID_SCOPE_KINDS)}"
        )

    scope_uuid = _uuid.UUID(str(scope_id))

    async with get_db_session_for_tenant(tenant_id) as session:
        result = await session.execute(
            text(
                "DELETE FROM role_bindings "
                "WHERE user_id = :user_id "
                "  AND scope_kind = :scope_kind "
                "  AND scope_id = :scope_id "
                "  AND role_name = :role_name"
            ),
            {
                "user_id": user_id,
                "scope_kind": scope_kind,
                "scope_id": scope_uuid,
                "role_name": role_name,
            },
        )

        # Only audit an actual removal. Revocation is idempotent, so a repeat call
        # deletes nothing; recording it anyway would fill the trail with revocations
        # that took away nothing and make the real one harder to find.
        if result.rowcount:
            await record_rbac_change(
                session,
                tenant_id=str(tenant_id),
                actor_id=revoked_by,
                event_type=RBAC_ROLE_REVOKED,
                subject_id=user_id,
                scope_kind=scope_kind,
                scope_id=str(scope_uuid),
                role=role_name,
            )

    logger.info(
        "revoke_role: user=%s scope=%s:%s role=%s tenant=%s (idempotent)",
        user_id,
        scope_kind,
        scope_uuid,
        role_name,
        tenant_id,
    )


async def _belongs_to_unit(
    session, scope_kind: str, scope_id: str, unit_id: str
) -> bool:
    """Is this project (or workstream) inside the given business unit?

    Used to decide whether a unit-owned custom role may be bound at a scope BELOW its
    unit — which it may, because a project inside the unit is part of what that unit
    administers. Anything outside the unit is not.
    """
    if scope_kind == "project":
        row = (await session.execute(
            text("SELECT workspace_id FROM projects WHERE id = CAST(:i AS uuid)"),
            {"i": scope_id},
        )).first()
        return row is not None and str(row.workspace_id) == unit_id
    if scope_kind == "workstream":
        row = (await session.execute(
            text(
                "SELECT p.workspace_id FROM workstreams w "
                "JOIN projects p ON p.id = w.project_id WHERE w.id = CAST(:i AS uuid)"
            ),
            {"i": scope_id},
        )).first()
        return row is not None and str(row.workspace_id) == unit_id
    return False


async def grant_custom_role(
    user_id: str,
    scope_id: _uuid.UUID | str,
    custom_role_id: _uuid.UUID | str,
    *,
    tenant_id: str,
    scope_kind: str = "business_unit",
) -> None:
    """Idempotently assign a tenant custom role to a user at a scope.

    Mirrors grant_role but references custom_role_id (role_name NULL). Writes via
    get_db_session_for_tenant so the RLS GUC is set — FORCE RLS on role_bindings
    and custom_roles makes this structurally incapable of crossing tenants.

    No tier check: a custom role carries no fixed tier, so there is nothing to conflict
    with. If custom roles later gain a tier, mirror grant_role's _assert_no_tier_conflict.
    """
    if not tenant_id:
        raise ValueError("tenant_id is required for grant_custom_role (fail-closed)")

    if scope_kind not in VALID_SCOPE_KINDS:
        raise ValueError(
            f"Unknown scope_kind '{scope_kind}'. Valid: {list(VALID_SCOPE_KINDS)}"
        )

    scope_uuid = _uuid.UUID(str(scope_id))
    role_uuid = _uuid.UUID(str(custom_role_id))
    tenant_uuid = _uuid.UUID(str(tenant_id))
    now = datetime.now(tz=timezone.utc)

    async with get_db_session_for_tenant(tenant_id) as session:
        # A business-unit-owned role is assignable only inside the unit that owns it.
        # Without this the owner scope would be decoration: a BU Admin could define a
        # role for their unit and then bind it at organization scope, which is exactly
        # the widening the scope column exists to prevent.
        owner = (await session.execute(
            text("SELECT scope_kind, scope_id FROM custom_roles WHERE id = :rid"),
            {"rid": str(role_uuid)},
        )).first()
        if owner is None:
            raise ValueError(f"unknown custom role {custom_role_id}")
        if owner.scope_kind == "business_unit":
            owning_unit = str(owner.scope_id)
            allowed_here = (
                (scope_kind == "business_unit" and str(scope_uuid) == owning_unit)
                or (
                    scope_kind in ("project", "workstream")
                    and await _belongs_to_unit(session, scope_kind, str(scope_uuid), owning_unit)
                )
            )
            if not allowed_here:
                raise ValueError(
                    f"custom role {custom_role_id} is owned by business unit {owning_unit} "
                    f"and cannot be assigned at {scope_kind} {scope_uuid}"
                )

        await session.execute(
            text(
                "INSERT INTO users (id, tenant_id, created_at) "
                "VALUES (:id, :tenant_id, :created_at) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": user_id, "tenant_id": str(tenant_uuid), "created_at": now},
        )
        await session.execute(
            text(
                "INSERT INTO role_bindings "
                "  (id, user_id, scope_kind, scope_id, custom_role_id, status, tenant_id, created_at) "
                "VALUES (:id, :user_id, :scope_kind, :scope_id, :custom_role_id, 'active', :tenant_id, :created_at) "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "id": str(_uuid.uuid4()),
                "user_id": user_id,
                "scope_kind": scope_kind,
                "scope_id": str(scope_uuid),
                "custom_role_id": str(role_uuid),
                "tenant_id": str(tenant_uuid),
                "created_at": now,
            },
        )
        await record_rbac_change(
            session,
            tenant_id=str(tenant_uuid),
            actor_id=None,
            event_type=RBAC_CUSTOM_ROLE_GRANTED,
            subject_id=user_id,
            scope_kind=scope_kind,
            scope_id=str(scope_uuid),
            role=str(role_uuid),
            extra={"custom_role_owner_scope": owner.scope_kind},
        )

    logger.info(
        "grant_custom_role: user=%s scope=%s:%s custom_role=%s tenant=%s",
        user_id, scope_kind, scope_uuid, role_uuid, tenant_uuid,
    )


# ---------------------------------------------------------------------------
# Operator CLI (D-09)
#
# Usage: python -m agentic_app.shared.authz.grant \
#          --user-id <sub> --workspace-id <uuid> --role <role_name> --tenant-id <uuid>
#
# WHY this is operator-only bootstrap:
#   role_bindings starts empty under deny-by-default; there is no login provider
#   until 7.3 (OIDC) / 7.4 (SCIM) so the first admin cannot be granted via HTTP.
#   The CLI gates --tenant-id as REQUIRED (not defaulted) so an operator must explicitly
#   supply it — the grant cannot be accidentally scoped to the wrong tenant.
#   The self-serve admin grant API/UI (gated by admin:* / a future rbac:manage) is
#   deferred to near 7.3/7.4 where real authenticated users exist (D-09 Deferred Ideas).
# ---------------------------------------------------------------------------

def _cli_main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m shared.authz.grant",
        description=(
            "Bootstrap operator: assign a role to a user at a scope.\n"
            "Writes via the tenant DB session (FORCE RLS). Idempotent (ON CONFLICT DO NOTHING).\n"
            "NO HTTP surface — operator-only (D-09)."
        ),
    )
    parser.add_argument("--user-id", required=True, help="JWT sub / external identity")
    parser.add_argument(
        "--scope-id", required=True, help="Target organization / business unit / project UUID"
    )
    parser.add_argument(
        "--scope-kind",
        default="business_unit",
        choices=list(VALID_SCOPE_KINDS),
        help="Which level the binding applies at (default: business_unit)",
    )
    parser.add_argument(
        "--role",
        required=True,
        choices=sorted(ALL_ROLES),
        help=f"Role to assign. One of: {', '.join(sorted(ALL_ROLES))}",
    )
    parser.add_argument(
        "--tenant-id",
        required=True,
        help="Org/tenant UUID (REQUIRED — no implicit default, T-7.2-20)",
    )
    args = parser.parse_args()

    asyncio.run(
        grant_role(
            user_id=args.user_id,
            scope_id=args.scope_id,
            role_name=args.role,
            tenant_id=args.tenant_id,
            scope_kind=args.scope_kind,
        )
    )
    print(
        f"Granted: user={args.user_id!r} role={args.role!r} "
        f"scope={args.scope_kind}:{args.scope_id!r} tenant={args.tenant_id!r}"
    )


if __name__ == "__main__":
    _cli_main()
