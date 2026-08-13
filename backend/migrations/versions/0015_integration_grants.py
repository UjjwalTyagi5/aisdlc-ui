"""Which Business Units may use which integration.

THE MISSING MIDDLE OF A THREE-LEVEL CASCADE. Two of the three levels already existed:
`connectors` / `mcp_servers` record what the ORGANISATION has onboarded, and
`projects.connectors` / `.mcp_servers` record what a PROJECT wired up. Nothing recorded
the permission in between — whether a unit may use the thing at all — so the
Integrations hub had no way to answer "which units hold Jira", and a project could
name any connector the org had onboarded.

`workspace_connectors` is not this table and does not substitute for it. That records
that a unit ENABLED a connector; this records that the organisation PERMITTED it to.
The distinction is the whole cascade: an Org Admin grants, a unit admin enables, a
project uses. Collapsing them would make enabling and being-allowed the same act, and
then only the person who can enable could be the person who decides.

ONE TABLE FOR BOTH KINDS. A connector is identified by its kind (`jira`) and an MCP
server by its uuid, so `target_ref` is text rather than two nullable typed columns.
They are the same decision made about two kinds of thing, and splitting them would
mean every reader unions two queries and every writer picks a table.

Revision ID: 0015_integration_grants
Revises: 0014_role_binding_extra_agents
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015_integration_grants"
down_revision = "0014_role_binding_extra_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_grants",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        # 'connector' | 'mcp' — which catalogue target_ref points into.
        sa.Column("kind", sa.String(length=16), nullable=False),
        # A connector kind ('jira') or an MCP server uuid, as text for both.
        sa.Column("target_ref", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("granted_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # The grant IS the key: a unit either has it or does not, and granting twice
        # is the same state. No surrogate id, so an idempotent grant is an upsert
        # rather than a read-then-insert with a race in the middle.
        sa.PrimaryKeyConstraint("tenant_id", "kind", "target_ref", "workspace_id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.CheckConstraint("kind IN ('connector', 'mcp')", name="ck_integration_grant_kind"),
    )
    # "Which units hold this integration" — the hub's own read.
    op.create_index(
        "ix_integration_grants_target",
        "integration_grants",
        ["tenant_id", "kind", "target_ref"],
    )
    # "What may this unit use" — the per-unit check on the project side.
    op.create_index(
        "ix_integration_grants_workspace",
        "integration_grants",
        ["tenant_id", "workspace_id"],
    )

    op.execute("ALTER TABLE integration_grants ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON integration_grants "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON integration_grants "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute("ALTER TABLE integration_grants FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                -- DELETE is the revoke. Taking a grant away has to be reachable, and
                -- the trail of who granted what lives in audit_events.
                GRANT SELECT, INSERT, UPDATE, DELETE ON integration_grants TO sdlc_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON integration_grants")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON integration_grants")
    op.drop_index("ix_integration_grants_workspace", table_name="integration_grants")
    op.drop_index("ix_integration_grants_target", table_name="integration_grants")
    op.drop_table("integration_grants")
