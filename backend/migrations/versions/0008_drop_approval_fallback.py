"""Remove fallback routing from approval requests.

`fallback_used` was added in 0007 in anticipation of routing that substitutes a
Project Admin (or escalates to a BU Admin) when nobody holds the request's
target_role. That behaviour is not wanted: a request that nobody can action stays
pending and visible rather than being handed to someone else.

The column goes with it. Left in place it would sit at false forever while implying a
substitution mechanism exists — and the next person to read the schema would
reasonably conclude some requests are decided by a fallback approver, which would not
be true of any row.

`target_role` stays. "Who should decide this" is meaningful whether or not anyone
currently holds the role — it is what makes an unactioned request diagnosable rather
than merely stuck.

Revision ID: 0008_drop_approval_fallback
Revises: 0007_approval_requests
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_drop_approval_fallback"
down_revision = "0007_approval_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("approval_requests", "fallback_used")


def downgrade() -> None:
    op.add_column(
        "approval_requests",
        sa.Column(
            "fallback_used",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
