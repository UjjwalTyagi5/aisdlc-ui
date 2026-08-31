"""Add 'project_activated' to the notifications.kind check constraint.

Fires when a pending project (migration 0028) is approved and a deferred
contributor is seated — see shared/governance/effects.py::_apply_project_creation.
Mirrors NotificationKind in frontend/lib/schemas/notification.ts.

Revision ID: 0029_notification_kind
Revises: 0028_project_approval
"""
from alembic import op

revision = "0029_notification_kind"
down_revision = "0028_project_approval"
branch_labels = None
depends_on = None

_OLD_KINDS = (
    "hitl_pending", "run_failed", "run_completed", "budget_near_cap",
    "guardrail_blocked", "mention", "request_created", "request_assigned",
    "request_approval_required", "request_approved", "request_rejected",
    "request_escalated", "member_awaiting_role",
)
_NEW_KINDS = _OLD_KINDS + ("project_activated",)


def upgrade() -> None:
    op.drop_constraint("ck_notification_kind", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notification_kind", "notifications",
        "kind IN (" + ", ".join(f"'{k}'" for k in _NEW_KINDS) + ")",
    )


def downgrade() -> None:
    # Delete what the older constraint cannot hold, BEFORE narrowing to it. Postgres
    # validates a CHECK against existing rows at ALTER time, so a single
    # `project_activated` notification made this downgrade fail outright — which is
    # what test_alembic_migration_cycle was catching.
    #
    # Expressed as NOT IN (_OLD_KINDS) rather than = 'project_activated' so that adding
    # a kind to _NEW_KINDS later cannot leave this line behind, silently correct for
    # the wrong set. A notification is a historical record, not state anything reads
    # back: losing the ones the older schema has no name for is the honest outcome.
    op.execute(
        "DELETE FROM notifications WHERE kind NOT IN ("
        + ", ".join(f"'{k}'" for k in _OLD_KINDS)
        + ")"
    )
    op.drop_constraint("ck_notification_kind", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notification_kind", "notifications",
        "kind IN (" + ", ".join(f"'{k}'" for k in _OLD_KINDS) + ")",
    )
