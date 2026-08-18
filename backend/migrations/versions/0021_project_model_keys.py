"""Project-level BYOK — a Project Admin may bring their own model key.

PRD §371/§1640/§1692/§1698: "a Project Admin may add a project-specific key only
if the Business Unit allows it." Extends the existing org-wide/BU-scoped
model_providers pattern (workspace_id) one level deeper (project_id), and adds
the BU-level opt-in policy this requires.

Revision ID: 0021_project_model_keys
Revises: 0020_model_call_guardrails
"""
import uuid as _uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0021_project_model_keys"
down_revision = "0020_model_call_guardrails"
branch_labels = None
depends_on = None


def _force_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        f"CREATE POLICY tenant_isolation_insert ON {table} "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def _grant_app(table: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO sdlc_app;
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    op.add_column(
        "model_providers",
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
    )
    op.create_index("ix_model_providers_project_id", "model_providers", ["project_id"])

    op.create_table(
        "bu_model_key_policy",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("allow_project_key", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("updated_by", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_unique_constraint(
        "uq_bu_model_key_policy", "bu_model_key_policy",
        ["tenant_id", "workspace_id", "provider", "model_id"],
    )
    op.create_index("ix_bu_model_key_policy_tenant_id", "bu_model_key_policy", ["tenant_id"])
    _force_rls("bu_model_key_policy")
    _grant_app("bu_model_key_policy")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON bu_model_key_policy")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON bu_model_key_policy")
    op.drop_table("bu_model_key_policy")

    op.drop_index("ix_model_providers_project_id", table_name="model_providers")
    op.drop_column("model_providers", "project_id")
