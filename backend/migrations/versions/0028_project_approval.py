"""Add projects.approval_status and the decided-by/at/reason columns.

A Project Admin's project sits `pending_approval` until the owning Business
Unit Admin decides it; Org Admin and BU Admin creations skip straight to
`active` — they're at or above the approving tier already. `active` is the
default so every existing project keeps parsing as live. See
shared/governance/effects.py::_apply_project_creation /
apply_on_reject for what flips these columns.

Revision ID: 0028_project_approval
Revises: 0027_merge_heads_3
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_project_approval"
down_revision = "0027_merge_heads_3"
branch_labels = None
depends_on = None

_STATUSES = "('active','pending_approval','rejected')"


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "approval_status", sa.String(length=20), nullable=False,
            server_default="active",
        ),
    )
    op.create_check_constraint(
        "ck_project_approval_status", "projects", f"approval_status IN {_STATUSES}"
    )
    # A display name, matching governance_requests.decided_by's own convention
    # (that column stores decider_name, not a user id).
    op.add_column("projects", sa.Column("approval_decided_by", sa.String(length=255), nullable=True))
    op.add_column("projects", sa.Column("approval_decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("projects", sa.Column("approval_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "approval_reason")
    op.drop_column("projects", "approval_decided_at")
    op.drop_column("projects", "approval_decided_by")
    op.drop_constraint("ck_project_approval_status", "projects", type_="check")
    op.drop_column("projects", "approval_status")
