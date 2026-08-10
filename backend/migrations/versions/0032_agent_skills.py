"""agent_skills + agent_skill_toggles — tenant-scoped skill content and enable/disable state.

Revision ID: 0032_agent_skills
Revises: 0031, 0031_merge_model_limits_pipeline

Merge revision: unifies the tree's two heads (0031 requirements_artifacts and
0031_merge_model_limits_pipeline) AND creates the two skills tables. Both tables
are tenant-scoped under FORCE ROW LEVEL SECURITY, copying 0027's exact policy SQL.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0032_agent_skills"
down_revision = ("0031", "0031_merge_model_limits_pipeline")
branch_labels = None
depends_on = None


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        f"CREATE POLICY tenant_isolation_insert ON {table} "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )


def upgrade() -> None:
    op.create_table(
        "agent_skills",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.String(50), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("scope_id", UUID(as_uuid=True), nullable=True),
        sa.Column("skill_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("when_to_use", sa.Text, nullable=True),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("runtime", sa.String(8), nullable=False, server_default="llm"),
        sa.Column("origin", sa.String(16), nullable=False, server_default="custom"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "tenant_id", "agent_id", "scope", "scope_id", "skill_key", "version",
            name="uq_agent_skill_version",
        ),
    )
    op.create_index("ix_agent_skills_tenant_id", "agent_skills", ["tenant_id"])
    op.create_index("ix_agent_skills_agent_id", "agent_skills", ["agent_id"])
    _enable_rls("agent_skills")

    op.create_table(
        "agent_skill_toggles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.String(50), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("scope_id", UUID(as_uuid=True), nullable=True),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("skill_key", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "tenant_id", "agent_id", "scope", "scope_id", "origin", "skill_key",
            name="uq_agent_skill_toggle",
        ),
    )
    op.create_index("ix_agent_skill_toggles_tenant_id", "agent_skill_toggles", ["tenant_id"])
    _enable_rls("agent_skill_toggles")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON agent_skill_toggles")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON agent_skill_toggles")
    op.drop_index("ix_agent_skill_toggles_tenant_id", table_name="agent_skill_toggles")
    op.drop_table("agent_skill_toggles")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON agent_skills")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON agent_skills")
    op.drop_index("ix_agent_skills_agent_id", table_name="agent_skills")
    op.drop_index("ix_agent_skills_tenant_id", table_name="agent_skills")
    op.drop_table("agent_skills")
