"""RBAC catalog + tenant-scoped assignment table + forced RLS + idempotent seed.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-08

Creates the four new RBAC tables required by REQ-M7-09:
  roles, permissions, role_permissions  — global catalog (no tenant_id, no RLS, D-03)
  users                                 — global identity table (non-RLS, D-08)
  user_workspace_roles                  — tenant-scoped assignment table (FORCE RLS, D-03/D-06)

IMPORTANT: user_workspace_roles is a NEW table — 0001_initial_schema.py never defined it.
Unlike 0006 (which only added FORCE to tables that 0001 already ENABLE'd + POLICY'd), this
migration must issue the FULL RLS lifecycle for user_workspace_roles:
  ALTER TABLE user_workspace_roles ENABLE ROW LEVEL SECURITY
  CREATE POLICY tenant_isolation ... USING (current_setting(..., true)::uuid)
  CREATE POLICY tenant_isolation_insert ... WITH CHECK (current_setting(..., true)::uuid)
  ALTER TABLE user_workspace_roles FORCE ROW LEVEL SECURITY

Omitting ENABLE + CREATE POLICY would leave the table wide-open despite FORCE (Pitfall 3).
The policy expression exactly matches 0001's shape so tests/ops tooling can verify uniformly.

Catalog seed is idempotent via ON CONFLICT DO NOTHING — safe to re-run (T-7.2-07).
user_workspace_roles rows are NOT seeded here: FORCE RLS subjects even the migrations
superuser to the policy, and app.current_tenant_id is unset during the migration
→ any INSERT would hit "new row violates row-level security policy" (Pitfall 5).
Real assignments come from the grant_role bootstrap helper (D-09, plan 03).

Must run as superuser (POSTGRES_MIGRATIONS_CONN_STRING) — same requirement as 0006.
"""
import uuid as _uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# The 6-role permission matrix from shared/authz/permissions.py — replicated literally
# so the migration is self-contained and not import-coupled to app code at runtime.
# MUST stay in sync with _ROLE_PERMISSIONS in shared/authz/permissions.py (D-01, D-07).
_ROLE_PERMS: dict[str, list[str]] = {
    "product_manager": [
        "run:create",
        "artifact:view",
        "artifact:approve_requirements",
    ],
    "tech_lead": [
        "artifact:approve_design",
        "artifact:approve_development",  # D-07: closes pipeline gap (Pitfall 6)
        "connector:manage",
        "artifact:view",
    ],
    "qa_lead": [
        "artifact:approve_testing",
        "artifact:view",
    ],
    "sre_lead": [
        "artifact:approve_deployment",  # forward-compat — no deployment phase yet
        "artifact:view",
    ],
    "developer": [
        "run:create",
        "artifact:view",
    ],
    "admin": [
        "admin:*",
    ],
}


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Step 1: Global catalog tables — no tenant_id, no RLS (D-03)
    # ------------------------------------------------------------------

    op.create_table(
        "roles",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("description", sa.String(255), nullable=True),
    )

    op.create_table(
        "permissions",
        sa.Column("name", sa.String(128), primary_key=True),
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_name", sa.String(64), sa.ForeignKey("roles.name"), primary_key=True),
        sa.Column("permission_name", sa.String(128), sa.ForeignKey("permissions.name"), primary_key=True),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.String(255), primary_key=True),  # == JWT sub
        sa.Column("email", sa.String(320), nullable=True, index=True),
        sa.Column("external_id", sa.String(255), nullable=True, index=True),  # SCIM linkage (7.4)
        # Informational only — not an RLS anchor; User is global (D-08)
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ------------------------------------------------------------------
    # Step 2: Tenant-scoped assignment table (D-03/D-06)
    # ------------------------------------------------------------------

    op.create_table(
        "user_workspace_roles",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            default=_uuid.uuid4,
        ),
        sa.Column("user_id", sa.String(255), nullable=False, index=True),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "role_name",
            sa.String(64),
            sa.ForeignKey("roles.name"),
            nullable=False,
        ),
        # RLS anchor: no FK intentional — policy column, not a relational FK (mirrors Project.tenant_id)
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "workspace_id", "role_name", name="uq_uwr_user_workspace_role"),
    )

    # ------------------------------------------------------------------
    # Step 3: Full RLS lifecycle for user_workspace_roles
    #
    # This table is NEW — 0001 never touched it — so ENABLE + CREATE POLICY
    # + FORCE are all needed here (Pitfall 3).  Do NOT include the original
    # five tables (projects/runs/artifacts/audit_events/agent_call_logs):
    # 0001 already ENABLE'd + POLICY'd them; 0006 FORCE'd them.
    # Re-authoring here would raise "policy already exists" for those tables.
    #
    # Policy expression matches 0001 exactly (A5 / RESEARCH Pitfall 3):
    #   current_setting('app.current_tenant_id', true)::uuid
    # The `true` flag makes current_setting return NULL when unset rather than
    # raising an error — the cast to ::uuid then yields NULL so the USING clause
    # evaluates to false, blocking all rows when no tenant GUC is set (safe default).
    # ------------------------------------------------------------------

    op.execute("ALTER TABLE user_workspace_roles ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON user_workspace_roles "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON user_workspace_roles "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute("ALTER TABLE user_workspace_roles FORCE ROW LEVEL SECURITY")

    # ------------------------------------------------------------------
    # Step 4: Idempotent catalog seed — roles, permissions, role_permissions
    #
    # ON CONFLICT DO NOTHING makes re-runs safe (T-7.2-07).
    # No user_workspace_roles rows seeded here — FORCE RLS + unset GUC during
    # migrations would reject them (Pitfall 5). Real assignments come from
    # grant_role() in shared/authz/grant.py (D-09).
    # ------------------------------------------------------------------

    for role, perms in _ROLE_PERMS.items():
        op.execute(
            f"INSERT INTO roles (name) VALUES ('{role}') ON CONFLICT (name) DO NOTHING"
        )
        for perm in perms:
            op.execute(
                f"INSERT INTO permissions (name) VALUES ('{perm}') ON CONFLICT (name) DO NOTHING"
            )
            op.execute(
                f"INSERT INTO role_permissions (role_name, permission_name) "
                f"VALUES ('{role}', '{perm}') ON CONFLICT DO NOTHING"
            )

    # ------------------------------------------------------------------
    # Step 5: One default workspace per existing org (D-06, idempotent)
    #
    # WHERE NOT EXISTS guards against re-runs (T-7.2-07).
    # gen_random_uuid() requires pgcrypto (pre-installed in Postgres 13+
    # as a built-in function without extension).
    # The LEGACY_TENANT_ID org seeded in 0006 gets exactly one workspace here.
    # ------------------------------------------------------------------

    op.execute(
        """
        INSERT INTO workspaces (id, organization_id, slug, display_name, created_at, updated_at)
        SELECT
            gen_random_uuid(),
            o.id,
            'default',
            'Default Workspace',
            now(),
            now()
        FROM organizations o
        WHERE NOT EXISTS (
            SELECT 1 FROM workspaces w
            WHERE w.organization_id = o.id
            AND w.slug = 'default'
        )
        """
    )

    print(
        "\n[0007] RBAC catalog tables created and seeded. "
        "user_workspace_roles: ENABLE + POLICY + FORCE RLS applied. "
        "One default workspace seeded per org (D-06)."
    )


def downgrade() -> None:
    # Drop the RLS policies added in upgrade() before dropping the table.
    # DROP POLICY IF EXISTS is safe if upgrade() was partially applied.
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_insert ON user_workspace_roles"
    )
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation ON user_workspace_roles"
    )

    # Drop tables in reverse FK dependency order.
    # catalog seed rows are removed with the tables (no separate DELETE needed).
    # Default workspaces are intentionally left in place — deleting them could
    # break FK references in projects; re-running upgrade() is idempotent via
    # WHERE NOT EXISTS, so orphaned default workspaces are harmless.
    op.drop_table("user_workspace_roles")
    op.drop_table("role_permissions")
    op.drop_table("users")
    op.drop_table("permissions")
    op.drop_table("roles")
