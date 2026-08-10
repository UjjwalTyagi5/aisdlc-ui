"""0019 workspace_connectors — per-workspace connector enablement table.

Each workspace independently enables/disables connectors. Credentials stay
per-tenant in Key Vault (backward compatible). This table tracks WHICH workspaces
have enabled which connector kinds, so the Integrations page shows workspace-specific
connector state rather than org-wide state.

Table: workspace_connectors
  id            UUID PK
  workspace_id  UUID FK → workspaces(id) ON DELETE CASCADE
  tenant_id     UUID NOT NULL (explicit tenant scoping, mirrors all RLS tables)
  kind          VARCHAR(64) — connector kind e.g. "azure_devops", "jira"
  enabled       BOOLEAN DEFAULT TRUE — soft-disable without removing
  created_at    TIMESTAMPTZ DEFAULT NOW()
  UNIQUE(workspace_id, kind)

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_connectors",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "kind", name="uq_workspace_connector_kind"),
    )
    op.create_index(
        "ix_workspace_connectors_workspace_id",
        "workspace_connectors",
        ["workspace_id"],
    )
    op.create_index(
        "ix_workspace_connectors_tenant_id",
        "workspace_connectors",
        ["tenant_id"],
    )

    # Enable RLS so workspaces can only see their own connector rows.
    op.execute("ALTER TABLE workspace_connectors ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspace_connectors FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_connectors_tenant_isolation
        ON workspace_connectors
        USING (tenant_id::text = current_setting('app.current_tenant_id', true))
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS workspace_connectors_tenant_isolation ON workspace_connectors")
    op.drop_index("ix_workspace_connectors_tenant_id", table_name="workspace_connectors")
    op.drop_index("ix_workspace_connectors_workspace_id", table_name="workspace_connectors")
    op.drop_table("workspace_connectors")
