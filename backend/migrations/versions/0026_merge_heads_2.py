"""Merge point: this branch's agent-access-foundation work (0024/0025) and main's
connector-access-level work (0023_connector_access_level) — two migrations both
numbered "0023" that independently descended from 0022_notification_scope
(0023_merge_heads as a genuine multi-parent merge, 0023_connector_access_level as a
plain single-parent migration that didn't know about it), diverging into two heads.

Revision ID: 0026_merge_heads_2
Revises: 0025_agent_access_override_grain, 0023_connector_access_level
"""
revision = "0026_merge_heads_2"
down_revision = ("0025_agent_access_override_grain", "0023_connector_access_level")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
