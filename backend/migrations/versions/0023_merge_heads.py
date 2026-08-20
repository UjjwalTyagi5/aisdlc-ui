"""Merge point: model-gateway branch (0020/0021, this branch) and the RBAC/auth
branch merged from main (0020_project_update_permission -> 0022_notification_scope).

Both branches independently created a migration numbered 0020 off the same parent
(0019_governance_decide) — expected, since they were developed in parallel on
different branches. No-op merge; each branch's own migrations already carry their
real schema changes.

Revision ID: 0023_merge_heads
Revises: 0021_project_model_keys, 0022_notification_scope
"""

revision = "0023_merge_heads"
down_revision = ("0021_project_model_keys", "0022_notification_scope")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
