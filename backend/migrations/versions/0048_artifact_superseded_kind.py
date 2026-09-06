"""Allow the `artifact_superseded` notification kind.

`notifications.kind` is CHECK-constrained to a known set, which is right: a typo'd
kind would deliver a notification nothing renders. It also means a new kind is a
migration, not just a call site.

WHAT WENT WRONG WITHOUT THIS, and why it is worth recording. Phase 5 emits a notice
when a published version is superseded. `emit` is documented as best-effort and wraps
its INSERT in `except Exception`, so the new kind should have failed harmlessly. It did
not: a failed statement ABORTS THE WHOLE POSTGRES TRANSACTION, so `emit` returned None
while every later statement in the caller's transaction died with
InFailedSQLTransactionError — the publication was rolled back BY ITS OWN ANNOUNCEMENT.
`emit` now wraps the insert in a SAVEPOINT so the promise it makes is actually true.

Revision ID: 0048_superseded_kind

Revises: 0047_consumption_grants
"""
from alembic import op

revision = "0048_superseded_kind"
down_revision = "0047_consumption_grants"
branch_labels = None
depends_on = None

_KINDS = (
    "hitl_pending", "run_failed", "run_completed", "budget_near_cap",
    "guardrail_blocked", "mention", "request_created", "request_assigned",
    "request_approval_required", "request_approved", "request_rejected",
    "request_escalated", "member_awaiting_role", "project_activated",
)
_NEW = "artifact_superseded"


def _rewrite(kinds: tuple[str, ...]) -> None:
    values = ", ".join(f"'{k}'" for k in kinds)
    op.execute("ALTER TABLE notifications DROP CONSTRAINT IF EXISTS ck_notification_kind")
    op.execute(
        "ALTER TABLE notifications ADD CONSTRAINT ck_notification_kind "
        f"CHECK (kind IN ({values}))"
    )


def upgrade() -> None:
    _rewrite(_KINDS + (_NEW,))


def downgrade() -> None:
    # Rows of the new kind would violate the narrower constraint, so they go first.
    # Deleting notifications is safe in a way deleting most things is not: they are a
    # record that something was ANNOUNCED, and the thing itself is still queryable.
    op.execute(f"DELETE FROM notifications WHERE kind = '{_NEW}'")
    _rewrite(_KINDS)
