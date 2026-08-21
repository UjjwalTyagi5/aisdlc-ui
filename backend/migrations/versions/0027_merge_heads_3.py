"""Merge point: this branch's per-stage tool access (0024_per_stage_tool_access) and
main's head (0026_merge_heads_2).

THE THIRD TIME THIS SHAPE HAS APPEARED, and for the third time git had nothing to
say about it. Both sides added a migration numbered "0024" — main's
0024_project_track descending from 0023_merge_heads, ours descending from
0023_connector_access_level — so the files never collided and `git merge` reported
zero conflicts while leaving alembic with two heads. A clean merge is not evidence
that the revision graph is intact; `alembic heads` is.

Nothing to run here: the two lineages touch different tables
(0024_per_stage_tool_access alters `projects` and `integration_grants`, main's
0024/0025 alter project tracks and agent-access overrides), so there is no state to
reconcile — only a single head to restore.

Revision ID: 0027_merge_heads_3
Revises: 0026_merge_heads_2, 0024_per_stage_tool_access
"""
revision = "0027_merge_heads_3"
down_revision = ("0026_merge_heads_2", "0024_per_stage_tool_access")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
