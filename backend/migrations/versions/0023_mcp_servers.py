"""0023 mcp_servers — tenant-registered MCP servers consumed as agent tools.

Each tenant registers MCP (Model Context Protocol) servers (streamable_http / sse /
stdio). Credentials (env vars / headers) stay in the secret store; only non-secret
refs are stored here (mirrors model_providers.secret_ref). Tenant-scoped under
FORCE RLS, same policy shape as workspace_connectors (0019) / model_providers (0015).

Table: mcp_servers
  id                   UUID PK
  tenant_id            UUID NOT NULL  (RLS anchor)
  server_name          VARCHAR(128) NOT NULL
  description          VARCHAR(512)
  transport            VARCHAR(32) NOT NULL  -- streamable_http | sse | stdio
  url                  VARCHAR(1024)         -- http/sse
  command              VARCHAR(512)          -- stdio
  args                 JSONB                 -- stdio
  env_vars_secret_ref  VARCHAR(255)
  headers_secret_ref   VARCHAR(255)
  is_active            BOOLEAN DEFAULT TRUE
  allowed_stages       JSONB                 -- agent ids; NULL = all stages
  tools_snapshot       JSONB                 -- cached {name, description}
  created_by           VARCHAR(255) NOT NULL
  created_at           TIMESTAMPTZ DEFAULT NOW()
  updated_at           TIMESTAMPTZ DEFAULT NOW()
  UNIQUE(tenant_id, server_name)

Revision ID: 0023
Revises: 0022
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0023"
# Re-chained after the product's 0023_agent_sessions (both originally branched off
# 0022 on separate branches). Linearizes to a single Alembic head on merge.
down_revision = "0023_agent_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("server_name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(512), nullable=True),
        sa.Column("transport", sa.String(32), nullable=False),
        sa.Column("url", sa.String(1024), nullable=True),
        sa.Column("command", sa.String(512), nullable=True),
        sa.Column("args", JSONB(), nullable=True),
        sa.Column("env_vars_secret_ref", sa.String(255), nullable=True),
        sa.Column("headers_secret_ref", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("allowed_stages", JSONB(), nullable=True),
        sa.Column("tools_snapshot", JSONB(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "server_name", name="uq_mcp_server_tenant_name"),
    )
    op.create_index("ix_mcp_servers_tenant_id", "mcp_servers", ["tenant_id"])

    op.execute("ALTER TABLE mcp_servers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE mcp_servers FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY mcp_servers_tenant_isolation
        ON mcp_servers
        USING (tenant_id::text = current_setting('app.current_tenant_id', true))
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS mcp_servers_tenant_isolation ON mcp_servers")
    op.drop_index("ix_mcp_servers_tenant_id", table_name="mcp_servers")
    op.drop_table("mcp_servers")
