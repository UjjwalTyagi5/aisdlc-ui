"""dev_workspaces — persistent per-project Development workspace

Revision ID: 0029_dev_workspaces
Revises: 0028_conversations
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0029_dev_workspaces"
down_revision = "0028_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dev_workspaces",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("ado_project", sa.String(200), nullable=False),
        sa.Column("repo_name", sa.String(200), nullable=False),
        sa.Column("branch", sa.String(200), nullable=False),
        sa.Column("remote_url", sa.String(500), nullable=False),
        sa.Column("work_dir", sa.String(500), nullable=False),
        sa.Column("commit_sha", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pulling"),
        sa.Column("pulled_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "project_id", name="uq_dev_workspace_project"),
    )
    op.create_index("ix_dev_workspaces_tenant_id", "dev_workspaces", ["tenant_id"])
    op.execute("ALTER TABLE dev_workspaces ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE dev_workspaces FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON dev_workspaces "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON dev_workspaces "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON dev_workspaces")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON dev_workspaces")
    op.drop_index("ix_dev_workspaces_tenant_id", table_name="dev_workspaces")
    op.drop_table("dev_workspaces")
