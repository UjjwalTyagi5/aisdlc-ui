"""agent_profiles — org-editable versioned agent behavior layer (D5, D6, DP3)

Revision ID: 0027_agent_profiles
Revises: 0026_mcp_server_capabilities
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0027_agent_profiles"
down_revision = "0026_mcp_server_capabilities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.String(50), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("scope_id", UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("prompt_prepend", sa.Text, nullable=True),
        sa.Column("prompt_append", sa.Text, nullable=True),
        sa.Column("enabled_capabilities", JSONB, nullable=True),
        sa.Column("disabled_curated", JSONB, nullable=True),
        sa.Column("primary_overrides", JSONB, nullable=True),
        sa.Column("thresholds", JSONB, nullable=True),
        sa.Column("reference_doc_summaries", JSONB, nullable=True),
        sa.Column("output_contract_extra", sa.Text, nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "agent_id", "scope", "scope_id", "version",
                            name="uq_agent_profile_scope_version"),
    )
    op.create_index("ix_agent_profiles_tenant_id", "agent_profiles", ["tenant_id"])
    op.create_index("ix_agent_profiles_agent_id", "agent_profiles", ["agent_id"])
    op.execute("ALTER TABLE agent_profiles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_profiles FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON agent_profiles "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON agent_profiles "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON agent_profiles")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON agent_profiles")
    op.drop_index("ix_agent_profiles_agent_id", table_name="agent_profiles")
    op.drop_index("ix_agent_profiles_tenant_id", table_name="agent_profiles")
    op.drop_table("agent_profiles")
