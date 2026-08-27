"""Merge point: this branch's Agent Studio import screening
(0035_import_source_allowlist, via 0034_agent_default_evaluations) and main's
Azure Communication Services email + project budget window work
(0035_project_budget_window, via 0034_settings_change_request).

THE FIFTH TIME THIS SHAPE HAS APPEARED. Both sides added a migration numbered
"0034" -- ours 0034_agent_default_evaluations, main's 0034_settings_change_request
-- both descending from the same parent (0033_project_integration_config), so the
files never collided and `git merge` reported zero conflicts while leaving alembic
with two heads. A clean merge is not evidence that the revision graph is intact;
`alembic heads` is.

Nothing to run here: the two lineages touch disjoint state. Ours adds two new
tables (agent_default_evaluations, import_source_allowlist). Main's
0034_settings_change_request widens governance_requests' existing type CHECK
constraint to admit "project_settings_change" -- the agent_default_org/_workspace/
_project members it also lists were already present in the 0010 baseline, so
there is no drift to reconcile there either -- and 0035_project_budget_window adds
two nullable date columns to projects. No shared table, no shared column, no
value either side depends on the other having written.

Revision ID: 0036_merge_heads_6
Revises: 0035_import_source_allowlist, 0035_project_budget_window
"""
revision = "0036_merge_heads_6"
down_revision = ("0035_import_source_allowlist", "0035_project_budget_window")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
