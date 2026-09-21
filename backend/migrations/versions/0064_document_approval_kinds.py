"""Allow the document-approval notification kinds.

`notifications.kind` is CHECK-constrained, so a new kind is two changes — the Python
that emits it and this constraint — and a kind missing here is refused at INSERT time
(see tests/test_db_enums_match_the_code.py, which pins the two together).

Adds `document_approval_required` (to the project's admins when a document is put
forward), and `document_approved` / `document_rejected` (to the person who put it
forward, when it is decided).

Revision ID: 0064_document_approval_kinds
Revises: 0063_audit_keyset_index
"""
from alembic import op

revision = "0064_document_approval_kinds"
down_revision = "0063_audit_keyset_index"
branch_labels = None
depends_on = None

_KINDS = (
    "hitl_pending", "run_failed", "run_completed", "budget_near_cap",
    "guardrail_blocked", "mention", "request_created", "request_assigned",
    "request_approval_required", "request_approved", "request_rejected",
    "request_escalated", "member_awaiting_role", "project_activated",
    "artifact_superseded",
)
_NEW = ("document_approval_required", "document_approved", "document_rejected")


def _rewrite(kinds: tuple[str, ...]) -> None:
    values = ", ".join(f"'{k}'" for k in kinds)
    op.execute("ALTER TABLE notifications DROP CONSTRAINT IF EXISTS ck_notification_kind")
    op.execute(
        "ALTER TABLE notifications ADD CONSTRAINT ck_notification_kind "
        f"CHECK (kind IN ({values}))"
    )


def upgrade() -> None:
    _rewrite(_KINDS + _NEW)


def downgrade() -> None:
    values = ", ".join(f"'{k}'" for k in _NEW)
    op.execute(f"DELETE FROM notifications WHERE kind IN ({values})")
    _rewrite(_KINDS)
