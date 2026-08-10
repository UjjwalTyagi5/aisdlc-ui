"""conversation_sessions + conversation_messages — F2 §11A chat transcript rail

Revision ID: 0028_conversations
Revises: 0027_agent_profiles
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0028_conversations"
down_revision = "0027_agent_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.String(128), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "scope_type", "scope_id", name="uq_conversation_scope"),
    )
    op.create_index("ix_conversation_sessions_tenant_id", "conversation_sessions", ["tenant_id"])
    op.create_index("ix_conversation_sessions_run_id", "conversation_sessions", ["run_id"])
    op.execute("ALTER TABLE conversation_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE conversation_sessions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON conversation_sessions "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON conversation_sessions "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversation_sessions.id"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("author_id", sa.String(255), nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False, server_default="markdown"),
        sa.Column("tool_calls", JSONB, nullable=True),
        sa.Column("artifact_refs", JSONB, nullable=True),
        sa.Column("citations", JSONB, nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("tokens_in", sa.Integer, nullable=True),
        sa.Column("tokens_out", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("dedup_key", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("session_id", "seq", name="uq_message_session_seq"),
    )
    op.create_index("ix_conversation_messages_tenant_id", "conversation_messages", ["tenant_id"])
    op.create_index("ix_conversation_messages_session_id", "conversation_messages", ["session_id"])
    op.execute("ALTER TABLE conversation_messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE conversation_messages FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON conversation_messages "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON conversation_messages "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    # Reverse order: messages first (has FK to sessions), then sessions
    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON conversation_messages")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON conversation_messages")
    op.drop_index("ix_conversation_messages_session_id", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_tenant_id", table_name="conversation_messages")
    op.drop_table("conversation_messages")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON conversation_sessions")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON conversation_sessions")
    op.drop_index("ix_conversation_sessions_run_id", table_name="conversation_sessions")
    op.drop_index("ix_conversation_sessions_tenant_id", table_name="conversation_sessions")
    op.drop_table("conversation_sessions")