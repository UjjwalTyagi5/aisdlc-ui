"""agent_sessions + orchestrator_state tables (Django persistence replacement)

Revision ID: 0023_agent_sessions
Revises: 0022
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0023_agent_sessions"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_sessions",
        sa.Column("session_id", sa.String(64), primary_key=True),
        sa.Column("agent_type", sa.String(50), nullable=False, server_default="requirements"),
        sa.Column("user_id", sa.String(64), nullable=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("requirements_payload", JSONB, nullable=True),
        sa.Column("design_artifacts", JSONB, nullable=True),
        sa.Column("development_artifacts", JSONB, nullable=True),
        sa.Column("testing_artifacts", JSONB, nullable=True),
        sa.Column("code_review_artifacts", JSONB, nullable=True),
        sa.Column("security_artifacts", JSONB, nullable=True),
        sa.Column("deployment_artifacts", JSONB, nullable=True),
        sa.Column("last_handoff_event", JSONB, nullable=True),
        sa.Column("current_stage", sa.String(50), nullable=True, server_default="ingestion"),
        sa.Column("artifact_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_sessions_user_id", "agent_sessions", ["user_id"])
    op.create_index("ix_agent_sessions_tenant_id", "agent_sessions", ["tenant_id"])

    op.create_table(
        "orchestrator_state",
        sa.Column("chat_session_id", sa.String(64), primary_key=True),
        sa.Column("current_active_agent", sa.String(50), nullable=True),
        sa.Column("current_batch_id", sa.String(64), nullable=True),
        sa.Column("pending_user_gate", sa.String(50), nullable=True),
        sa.Column("last_handoff_event", JSONB, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("orchestrator_state")
    op.drop_index("ix_agent_sessions_tenant_id", table_name="agent_sessions")
    op.drop_index("ix_agent_sessions_user_id", table_name="agent_sessions")
    op.drop_table("agent_sessions")
