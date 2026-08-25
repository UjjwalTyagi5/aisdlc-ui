"""Merge point: this branch's model-provider grant kind (0028_model_provider_grant_kind)
and main's head (0030_credential_label, via 0028_project_approval/0029_notification_kind).

THE FOURTH TIME THIS SHAPE HAS APPEARED. Both sides added a migration numbered "0028" —
main's 0028_project_approval descending from 0027_merge_heads_3, ours descending from the
same parent — so the files never collided and `git merge` reported zero conflicts while
leaving alembic with two heads. A clean merge is not evidence that the revision graph is
intact; `alembic heads` is.

Nothing to run here: the two lineages touch different tables (0028_model_provider_grant_kind
widens integration_grants' kind CHECK constraint; main's 0028-0030 add project-approval
state, a notification kind, and a credential label column), so there is no state to
reconcile — only a single head to restore.

Revision ID: 0031_merge_heads_5
Revises: 0028_model_provider_grant_kind, 0030_credential_label
"""
revision = "0031_merge_heads_5"
down_revision = ("0028_model_provider_grant_kind", "0030_credential_label")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
