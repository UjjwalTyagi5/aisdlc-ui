"""Tech stacks: Business Unit alternatives and each project's choice (Agent Studio).

A Business Unit offers stacks and marks at most one its default; a project picks one or keeps
its own. Two tables, tenant RLS like every table added after the baseline. No role or
permission rows: ownership is Agent Studio's existing tier rule, so no RBAC catalogue drift.

Revision ID: 0065_tech_stacks
Revises: 0064_document_approval_kinds
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0065_tech_stacks"
down_revision = "0064_document_approval_kinds"
branch_labels = None
depends_on = None

_TABLES = ("tech_stacks", "project_tech_stack_selections")


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} "
               "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)")
    op.execute(f"CREATE POLICY tenant_isolation_insert ON {table} "
               "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO sdlc_app;
            END IF;
        END
        $$;
    """)


def upgrade() -> None:
    op.create_table(
        "tech_stacks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                  nullable=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("categories", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("updated_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("scope IN ('workspace', 'project')", name="ck_tech_stack_scope"),
        sa.CheckConstraint(
            "(scope = 'workspace' AND workspace_id IS NOT NULL AND project_id IS NULL) OR "
            "(scope = 'project' AND project_id IS NOT NULL AND workspace_id IS NULL)",
            name="ck_tech_stack_scope_ids"),
        sa.CheckConstraint("NOT is_default OR scope = 'workspace'", name="ck_tech_stack_default_is_bu"),
    )
    op.create_index("ix_tech_stacks_tenant_id", "tech_stacks", ["tenant_id"])
    op.create_index("ix_tech_stacks_workspace_id", "tech_stacks", ["workspace_id"])
    op.create_index("ix_tech_stacks_project_id", "tech_stacks", ["project_id"])
    # One live default per Business Unit; one live name per tier (case-insensitive).
    op.execute("CREATE UNIQUE INDEX uq_tech_stack_default ON tech_stacks (workspace_id) "
               "WHERE is_default AND deleted_at IS NULL")
    op.execute("CREATE UNIQUE INDEX uq_tech_stack_name ON tech_stacks "
               "(scope, COALESCE(workspace_id, project_id), lower(name)) WHERE deleted_at IS NULL")

    op.create_table(
        "project_tech_stack_selections",
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tech_stack_id", UUID(as_uuid=True), sa.ForeignKey("tech_stacks.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("selected_by", sa.String(255), nullable=True),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_project_tech_stack_selections_tenant_id", "project_tech_stack_selections", ["tenant_id"])

    for table in _TABLES:
        _rls(table)


def downgrade() -> None:
    op.drop_table("project_tech_stack_selections")
    op.drop_table("tech_stacks")
