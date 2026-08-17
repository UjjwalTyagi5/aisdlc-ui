"""Notifications — the bell had nowhere to deliver to.

Every transition this platform cares about already produces a record: a governance
request is raised, routed, decided, escalated. What was missing was the other half —
telling the person it now falls to. Without this table "your request was approved"
had nowhere to be written, so the bell was a counter with an illustrative dropdown
behind it.

ADDRESSED, NEVER BROADCAST. A row names an audience: a PERSON (`recipient_user_id`)
or a ROLE (`recipient_role`), and the CHECK constraint refuses a row that names
neither. One global list would show a Developer the Organization Admin's escalations,
which is both noise and a scope leak — and the leak is the serious half, because a
request title says which unit is over budget.

Both kinds exist because they answer different questions. "Your request was approved"
belongs to one identity and follows them. "A request is waiting on the Business Unit
Admin" belongs to whoever holds that role right now — including someone appointed
after it was raised, who would never see a notification addressed to their
predecessor.

READ STATE IS PER ROW, not per person, and that is a deliberate simplification worth
naming: a role-addressed notification marked read by one holder is read for all of
them. Two admins sharing a queue is the normal case here, and "somebody dealt with
it" is usually the truth you want. If per-person read state is ever needed it is a
join table, not a column change.

Revision ID: 0013_notifications
Revises: 0012_drop_temporal_columns
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013_notifications"
down_revision = "0012_drop_temporal_columns"
branch_labels = None
depends_on = None


# Mirrors NotificationKind in frontend/lib/schemas/notification.ts. The six
# `request_*` kinds are separate rather than one `request_update` with a payload
# because they reach different people for different reasons — assigned and
# approval_required go to the approver, the rest to the initiator — and collapsing
# them would make "who should hear about this" a runtime question instead of a
# property of the event.
KINDS = (
    "hitl_pending",
    "run_failed",
    "run_completed",
    "budget_near_cap",
    "guardrail_blocked",
    "mention",
    "request_created",
    "request_assigned",
    "request_approval_required",
    "request_approved",
    "request_rejected",
    "request_escalated",
    "member_awaiting_role",
)


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        # Where clicking it goes. A notification with no destination is a fact
        # nobody can act on, but it is not worth refusing one — some kinds are
        # genuinely informational.
        sa.Column("href", sa.String(length=512), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        # Exactly one of these is normally set; both is allowed and means "this
        # person, and anyone else holding that role".
        sa.Column("recipient_user_id", sa.String(length=255), nullable=True),
        sa.Column("recipient_role", sa.String(length=64), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN (" + ", ".join(f"'{k}'" for k in KINDS) + ")",
            name="ck_notification_kind",
        ),
        # The rule this table exists to enforce. A row addressed to nobody would be
        # delivered to everybody by any listing query that forgot to exclude it.
        sa.CheckConstraint(
            "recipient_user_id IS NOT NULL OR recipient_role IS NOT NULL",
            name="ck_notification_has_recipient",
        ),
    )
    # The bell's own read: this person's notifications, newest first.
    op.create_index(
        "ix_notifications_for_user",
        "notifications",
        ["tenant_id", "recipient_user_id", "created_at"],
    )
    op.create_index(
        "ix_notifications_for_role",
        "notifications",
        ["tenant_id", "recipient_role", "created_at"],
    )

    op.execute("ALTER TABLE notifications ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON notifications "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON notifications "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute("ALTER TABLE notifications FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                -- UPDATE for marking read. DELETE so a tenant's bell can be pruned;
                -- this is a delivery mechanism, not an audit trail — audit_events is
                -- where the permanent record lives, and it is append-only.
                GRANT SELECT, INSERT, UPDATE, DELETE ON notifications TO sdlc_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON notifications")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON notifications")
    op.drop_index("ix_notifications_for_role", table_name="notifications")
    op.drop_index("ix_notifications_for_user", table_name="notifications")
    op.drop_table("notifications")
