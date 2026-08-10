"""Idempotent role-assignment bootstrap helper + operator CLI (D-09).

grant_role(user_id, workspace_id, role_name, *, tenant_id):
  Writes a UserWorkspaceRole row via get_db_session_for_tenant (sets the RLS GUC).
  A plain/superuser session is BLOCKED by FORCE RLS on user_workspace_roles (Pitfall 5).
  ON CONFLICT (user_id, workspace_id, role_name) DO NOTHING — idempotent re-runs.
  Also upserts the User row if absent (D-08: global identity, no RLS, SCIM-forward).

This is the ONLY write path into user_workspace_roles in 7.2.  It is operator-only
(no HTTP route, no UI) and must never be imported from request-handling code.
The self-serve admin assignment API/UI is deferred to near 7.3/7.4 (D-09 Deferred Ideas).

CLI usage (operator-only):
  python -m agentic_app.shared.authz.grant \\
    --user-id <sub> --workspace-id <uuid> --role <role_name> --tenant-id <uuid>

Structurally incapable of crossing tenants: write goes through get_db_session_for_tenant
which sets app.current_tenant_id; FORCE RLS on user_workspace_roles enforces the policy.
"""
from __future__ import annotations

import asyncio
import logging
import uuid as _uuid
from datetime import datetime, timezone

from sqlalchemy import text

from shared.authz.permissions import ALL_ROLES
from shared.db import get_db_session_for_tenant

logger = logging.getLogger(__name__)


async def grant_role(
    user_id: str,
    workspace_id: _uuid.UUID | str,
    role_name: str,
    *,
    tenant_id: str,
) -> None:
    """Idempotently assign role_name to user_id in workspace_id under tenant_id.

    Args:
        user_id:      JWT sub / external identity string — must be non-empty.
        workspace_id: UUID of the workspace to assign the role in.
        role_name:    Must be one of ALL_ROLES from shared.authz.permissions.
        tenant_id:    The org/tenant UUID string — REQUIRED and NEVER empty.
                      An empty tenant would write under an unset GUC, which FORCE RLS
                      would reject anyway, but we fail-close explicitly (mirrors the
                      resolver's fail-closed contract).

    Why the tenant-scoped session and not a superuser/bypass-RLS session:
      FORCE RLS on user_workspace_roles means even a privileged connection would need
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

    workspace_uuid = _uuid.UUID(str(workspace_id))
    tenant_uuid = _uuid.UUID(str(tenant_id))
    new_uwr_id = _uuid.uuid4()
    now = datetime.now(tz=timezone.utc)

    async with get_db_session_for_tenant(tenant_id) as session:
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

        # (b) Assign the role — ON CONFLICT on the unique key (user_id, workspace_id, role_name).
        # The unique constraint is named uq_uwr_user_workspace_role in migration 0007
        # but ON CONFLICT (col list) is portable across constraint naming conventions.
        await session.execute(
            text(
                "INSERT INTO user_workspace_roles "
                "  (id, user_id, workspace_id, role_name, tenant_id, created_at) "
                "VALUES "
                "  (:id, :user_id, :workspace_id, :role_name, :tenant_id, :created_at) "
                "ON CONFLICT (user_id, workspace_id, role_name) DO NOTHING"
            ),
            {
                "id": str(new_uwr_id),
                "user_id": user_id,
                "workspace_id": str(workspace_uuid),
                "role_name": role_name,
                "tenant_id": str(tenant_uuid),
                "created_at": now,
            },
        )

    logger.info(
        "grant_role: user=%s workspace=%s role=%s tenant=%s (idempotent)",
        user_id,
        workspace_uuid,
        role_name,
        tenant_uuid,
    )


async def revoke_role(
    user_id: str,
    workspace_id: _uuid.UUID | str,
    role_name: str,
    *,
    tenant_id: str,
) -> None:
    """Remove role_name from user_id in workspace_id under tenant_id (idempotent).

    Mirror of grant_role: the DELETE runs through get_db_session_for_tenant so the
    RLS GUC (app.current_tenant_id) is set — FORCE RLS on user_workspace_roles makes
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

    workspace_uuid = _uuid.UUID(str(workspace_id))

    async with get_db_session_for_tenant(tenant_id) as session:
        await session.execute(
            text(
                "DELETE FROM user_workspace_roles "
                "WHERE user_id = :user_id "
                "  AND workspace_id = :workspace_id "
                "  AND role_name = :role_name"
            ),
            {
                "user_id": user_id,
                "workspace_id": workspace_uuid,
                "role_name": role_name,
            },
        )

    logger.info(
        "revoke_role: user=%s workspace=%s role=%s tenant=%s (idempotent)",
        user_id,
        workspace_uuid,
        role_name,
        tenant_id,
    )


async def grant_custom_role(
    user_id: str,
    workspace_id: _uuid.UUID | str,
    custom_role_id: _uuid.UUID | str,
    *,
    tenant_id: str,
) -> None:
    """Idempotently assign a tenant custom role to a user in a workspace.

    Mirrors grant_role but references custom_role_id (role_name NULL). Writes via
    get_db_session_for_tenant so the RLS GUC is set — FORCE RLS on user_workspace_roles
    and custom_roles makes this structurally incapable of crossing tenants.
    """
    if not tenant_id:
        raise ValueError("tenant_id is required for grant_custom_role (fail-closed)")

    workspace_uuid = _uuid.UUID(str(workspace_id))
    role_uuid = _uuid.UUID(str(custom_role_id))
    tenant_uuid = _uuid.UUID(str(tenant_id))
    now = datetime.now(tz=timezone.utc)

    async with get_db_session_for_tenant(tenant_id) as session:
        await session.execute(
            text(
                "INSERT INTO users (id, tenant_id, created_at) "
                "VALUES (:id, :tenant_id, :created_at) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": user_id, "tenant_id": str(tenant_uuid), "created_at": now},
        )
        await session.execute(
            text(
                "INSERT INTO user_workspace_roles "
                "  (id, user_id, workspace_id, custom_role_id, tenant_id, created_at) "
                "VALUES (:id, :user_id, :workspace_id, :custom_role_id, :tenant_id, :created_at) "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "id": str(_uuid.uuid4()),
                "user_id": user_id,
                "workspace_id": str(workspace_uuid),
                "custom_role_id": str(role_uuid),
                "tenant_id": str(tenant_uuid),
                "created_at": now,
            },
        )
    logger.info(
        "grant_custom_role: user=%s workspace=%s custom_role=%s tenant=%s",
        user_id, workspace_uuid, role_uuid, tenant_uuid,
    )


# ---------------------------------------------------------------------------
# Operator CLI (D-09)
#
# Usage: python -m agentic_app.shared.authz.grant \
#          --user-id <sub> --workspace-id <uuid> --role <role_name> --tenant-id <uuid>
#
# WHY this is operator-only bootstrap:
#   user_workspace_roles starts empty under deny-by-default; there is no login provider
#   until 7.3 (OIDC) / 7.4 (SCIM) so the first admin cannot be granted via HTTP.
#   The CLI gates --tenant-id as REQUIRED (not defaulted) so an operator must explicitly
#   supply it — the grant cannot be accidentally scoped to the wrong tenant.
#   The self-serve admin grant API/UI (gated by admin:* / a future rbac:manage) is
#   deferred to near 7.3/7.4 where real authenticated users exist (D-09 Deferred Ideas).
# ---------------------------------------------------------------------------

def _cli_main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m agentic_app.shared.authz.grant",
        description=(
            "Bootstrap operator: assign a role to a user in a workspace.\n"
            "Writes via the tenant DB session (FORCE RLS). Idempotent (ON CONFLICT DO NOTHING).\n"
            "NO HTTP surface — operator-only (D-09)."
        ),
    )
    parser.add_argument("--user-id", required=True, help="JWT sub / external identity")
    parser.add_argument(
        "--workspace-id", required=True, help="Target workspace UUID"
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
            workspace_id=args.workspace_id,
            role_name=args.role,
            tenant_id=args.tenant_id,
        )
    )
    print(
        f"Granted: user={args.user_id!r} role={args.role!r} "
        f"workspace={args.workspace_id!r} tenant={args.tenant_id!r}"
    )


if __name__ == "__main__":
    _cli_main()
