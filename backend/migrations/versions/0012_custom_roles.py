"""Custom (tenant-defined) roles — 2 FORCE-RLS tables + additive UWR ref column.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-11

Adds custom_roles + custom_role_permissions (tenant-scoped, full RLS lifecycle
identical to 0007's shape) and an ADDITIVE nullable custom_role_id on the
membership table so a row references EITHER a global default role (role_name)
OR a tenant custom role (custom_role_id), enforced by a CHECK.

NO row DML on the FORCE-RLS membership table — only DDL (ADD COLUMN, drop NOT NULL,
ADD CHECK, ADD FK) which RLS does not block (Pitfall 5 avoided). New-table policy
expression matches 0007 exactly: current_setting('app.current_tenant_id', true)::uuid.

Must run as the migrations role (POSTGRES_MIGRATIONS_CONN_STRING).
"""
import uuid as _uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_custom_role_tenant_name"),
    )

    op.create_table(
        "custom_role_permissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4),
        sa.Column(
            "custom_role_id", UUID(as_uuid=True),
            sa.ForeignKey("custom_roles.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("permission_name", sa.String(128), sa.ForeignKey("permissions.name"), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.UniqueConstraint("custom_role_id", "permission_name", name="uq_custom_role_perm"),
    )

    # Literal per-table DDL (not an f-string loop) so the FORCE-RLS lifecycle
    # text matches 0007's shape verbatim. Policy expression identical to 0007:
    # current_setting('app.current_tenant_id', true)::uuid.
    op.execute("ALTER TABLE custom_roles ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON custom_roles "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON custom_roles "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute("ALTER TABLE custom_roles FORCE ROW LEVEL SECURITY")

    op.execute("ALTER TABLE custom_role_permissions ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON custom_role_permissions "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON custom_role_permissions "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute("ALTER TABLE custom_role_permissions FORCE ROW LEVEL SECURITY")

    op.add_column(
        "user_workspace_roles",
        sa.Column(
            "custom_role_id", UUID(as_uuid=True),
            sa.ForeignKey("custom_roles.id", ondelete="CASCADE"), nullable=True,
        ),
    )
    op.alter_column("user_workspace_roles", "role_name", existing_type=sa.String(64), nullable=True)
    op.create_index("ix_uwr_custom_role_id", "user_workspace_roles", ["custom_role_id"])
    op.create_check_constraint(
        "ck_uwr_exactly_one_role",
        "user_workspace_roles",
        "(role_name IS NOT NULL AND custom_role_id IS NULL) "
        "OR (role_name IS NULL AND custom_role_id IS NOT NULL)",
    )

    print("\n[0012] custom_roles + custom_role_permissions (FORCE RLS) created; "
          "user_workspace_roles.custom_role_id added (additive, no row DML).")


def downgrade() -> None:
    op.drop_constraint("ck_uwr_exactly_one_role", "user_workspace_roles", type_="check")
    op.drop_index("ix_uwr_custom_role_id", table_name="user_workspace_roles")
    op.drop_column("user_workspace_roles", "custom_role_id")
    op.alter_column("user_workspace_roles", "role_name", existing_type=sa.String(64), nullable=False)
    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON custom_role_permissions")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON custom_role_permissions")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON custom_roles")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON custom_roles")
    op.drop_table("custom_role_permissions")
    op.drop_table("custom_roles")
