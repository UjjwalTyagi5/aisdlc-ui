"""Notifications: a role address needs a scope, or it reaches every unit.

`recipient_role = 'bu_admin'` named a QUEUE but not WHICH ONE. Inside a tenant every
Business Unit Admin matched it, so the Lending admin read "Role needed: Ana in
Payments" — and a request title is exactly the kind of fact the addressed-never-
broadcast rule exists to keep inside a unit. The 0013 design was right that a queue
belongs to whoever holds the role rather than to a person; it was incomplete in
naming the role without naming where the role is held.

WHAT A SCOPE MEANS HERE is "the queue for this role within this unit or project",
not "everyone under this scope". The role still decides WHO; the scope decides WHICH
of them. Both columns are nullable because one role genuinely has no scope: the
Organization Admin's queue is the organization, and there is exactly one.

NO CHECK CONSTRAINT requiring a scope, deliberately, and this is the one compromise
in this migration. Rows written before this can no longer be attributed to a unit —
nothing links a notification back to the thing that caused it — so a constraint
would either fail to create or force those rows to be destroyed. They keep the old
tenant-wide behaviour, which is exactly the leak, but they are a bounded and
shrinking set. The rule is enforced instead in `services/notifications.emit`, which
refuses a scope-less address for any role that is held at a scope. That covers every
row written from here on, which is the set that matters.

To close the legacy rows in an environment where the unit IS known, scope them by
hand — e.g. UPDATE notifications SET recipient_scope_kind = 'business_unit',
recipient_scope_id = '<unit>' WHERE recipient_role = 'bu_admin' AND
recipient_scope_id IS NULL.

Revision ID: 0022_notification_scope
Revises: 0021_password_reset_tokens
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0022_notification_scope"
down_revision = "0021_password_reset_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        # 'business_unit' | 'project'. Not constrained to those two: it mirrors
        # role_bindings.scope_kind, which is where the vocabulary is owned, and a
        # second place to change it is a second place to forget.
        sa.Column("recipient_scope_kind", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("recipient_scope_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # The role listing's real shape now — role AND scope together. The 0013 index on
    # (tenant_id, recipient_role, created_at) stays useful for the unscoped org-admin
    # queue, so this is added alongside it rather than replacing it.
    op.create_index(
        "ix_notifications_for_role_scope",
        "notifications",
        ["tenant_id", "recipient_role", "recipient_scope_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_for_role_scope", table_name="notifications")
    op.drop_column("notifications", "recipient_scope_id")
    op.drop_column("notifications", "recipient_scope_kind")
