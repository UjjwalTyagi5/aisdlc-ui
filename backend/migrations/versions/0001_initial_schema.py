"""Initial schema: all 7 tables + RLS tenant isolation policies.

Revision ID: 0001
Revises: None
Create Date: 2026-05-27
"""
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_RLS_TABLES = ("projects", "runs", "artifacts", "audit_events", "agent_call_logs")


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("slug", sa.String(128), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "workspaces",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("external_ref", sa.String(255), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("provider_kind", sa.String(50), nullable=False, server_default="azure_devops"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(50), nullable=False, server_default="requirements"),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("requirements_payload", JSONB, nullable=True),
        sa.Column("design_artifacts", JSONB, nullable=True),
        sa.Column("development_artifacts", JSONB, nullable=True),
        sa.Column("testing_artifacts", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "artifacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_type", sa.String(100), nullable=False),
        sa.Column("blob_url", sa.Text, nullable=True),
        sa.Column("blob_path", sa.Text, nullable=True),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("size_bytes", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=True),
        sa.Column("resource_id", sa.String(255), nullable=True),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "agent_call_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=True),
        sa.Column("agent_type", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=True),
        sa.Column("output_tokens", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # Indexes
    op.create_index("ix_org_slug", "organizations", ["slug"])
    op.create_index("ix_ws_org_id", "workspaces", ["organization_id"])
    op.create_index("ix_proj_ws_id", "projects", ["workspace_id"])
    op.create_index("ix_proj_tenant_id", "projects", ["tenant_id"])
    op.create_index("ix_run_project_id", "runs", ["project_id"])
    op.create_index("ix_run_tenant_id", "runs", ["tenant_id"])
    op.create_index("ix_artifact_run_id", "artifacts", ["run_id"])
    op.create_index("ix_artifact_tenant_id", "artifacts", ["tenant_id"])
    op.create_index("ix_audit_tenant_id", "audit_events", ["tenant_id"])
    op.create_index("ix_audit_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_created_at", "audit_events", ["created_at"])
    op.create_index("ix_acl_tenant_id", "agent_call_logs", ["tenant_id"])
    op.create_index("ix_acl_run_id", "agent_call_logs", ["run_id"])
    op.create_index("ix_acl_created_at", "agent_call_logs", ["created_at"])

    # RLS policies — must run as superuser (guaranteed by POSTGRES_MIGRATIONS_CONN_STRING).
    # current_setting with true flag returns NULL when unset (safe default — no rows leak).
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
        )
        op.execute(
            f"CREATE POLICY tenant_isolation_insert ON {table} "
            f"WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    # Drop RLS policies in reverse table order before dropping tables
    for table in reversed(_RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_insert ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Drop indexes explicitly (Postgres drops column indexes with the table,
    # but named indexes created via op.create_index need explicit removal)
    op.drop_index("ix_acl_created_at", table_name="agent_call_logs")
    op.drop_index("ix_acl_run_id", table_name="agent_call_logs")
    op.drop_index("ix_acl_tenant_id", table_name="agent_call_logs")
    op.drop_index("ix_audit_created_at", table_name="audit_events")
    op.drop_index("ix_audit_event_type", table_name="audit_events")
    op.drop_index("ix_audit_tenant_id", table_name="audit_events")
    op.drop_index("ix_artifact_tenant_id", table_name="artifacts")
    op.drop_index("ix_artifact_run_id", table_name="artifacts")
    op.drop_index("ix_run_tenant_id", table_name="runs")
    op.drop_index("ix_run_project_id", table_name="runs")
    op.drop_index("ix_proj_tenant_id", table_name="projects")
    op.drop_index("ix_proj_ws_id", table_name="projects")
    op.drop_index("ix_ws_org_id", table_name="workspaces")
    op.drop_index("ix_org_slug", table_name="organizations")

    # Drop tables in reverse FK dependency order
    op.drop_table("agent_call_logs")
    op.drop_table("audit_events")
    op.drop_table("artifacts")
    op.drop_table("runs")
    op.drop_table("projects")
    op.drop_table("workspaces")
    op.drop_table("organizations")
