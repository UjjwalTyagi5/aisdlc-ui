"""conversation_sessions: agent_id + title + per-user/agent/project list index

Enables the ChatGPT-style per-user, per-agent session rail (blueprint §11A): the
agent-scoped chat list filtered by (tenant, created_by, agent_id, project_id),
ordered by recency. Additive + forward-only — existing rows get NULL agent_id/title.

Revision ID: 0029_conversation_agent_list
Revises: 0028_conversations
"""
from alembic import op
import sqlalchemy as sa

revision = "0029_conversation_agent_list"
down_revision = "0028_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversation_sessions", sa.Column("agent_id", sa.String(32), nullable=True))
    op.add_column("conversation_sessions", sa.Column("title", sa.String(255), nullable=True))
    op.create_index(
        "ix_conv_user_agent",
        "conversation_sessions",
        ["tenant_id", "created_by", "agent_id", "project_id", "status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conv_user_agent", table_name="conversation_sessions")
    op.drop_column("conversation_sessions", "title")
    op.drop_column("conversation_sessions", "agent_id")
