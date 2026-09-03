"""Give artifacts an approval state, so a project admin gates what reaches shared storage.

WHY THE COLUMNS DID NOT EXIST. `ArtifactOut.status` has always been the literal string
"approved" — every artifact reported the same status because none of them HAD one. The
Artifact table carried no approval fields at all, so "approved" was a placeholder that
read like a fact.

WHAT THE GATE IS FOR. A generated document is downloadable from chat the moment it is
written. Putting it in the project's artifacts is a separate act with a separate
consequence: it becomes part of the project's shared, durable record. That decision now
belongs to whoever runs the project, not to the person who happened to be chatting.

`approval_status`:
    pending   — generated, bytes parked under the tenant's _pending prefix, not yet
                part of the project's record
    approved  — an admin accepted it; the bytes were moved to the real hierarchy path
    rejected  — an admin refused it; the pending bytes are deleted

EXISTING ROWS BECOME 'approved', NOT 'pending'. They predate the gate and are already
listed, downloaded and linked to; making them pending would retroactively withdraw
documents people are using and present a queue of historical items nobody decided to
submit. The gate applies to what happens next.

The server_default is 'pending' so a row inserted by code that has not been taught about
the column fails CLOSED — unapproved — rather than silently entering the shared record.
"""
import sqlalchemy as sa
from alembic import op

revision = "0040_artifact_approval"
down_revision = "0039_artifact_delete_permission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "artifacts",
        sa.Column(
            "approval_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column("artifacts", sa.Column("approved_by", sa.String(length=255), nullable=True))
    op.add_column(
        "artifacts",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("artifacts", sa.Column("rejection_reason", sa.Text(), nullable=True))

    # Everything that already exists was created before the gate — see the module
    # docstring. Backfill BEFORE anything can insert a pending row.
    op.execute("UPDATE artifacts SET approval_status = 'approved' WHERE approval_status = 'pending'")

    # The approvals queue is "pending artifacts for these projects", so the filter is
    # this column and the ordering is created_at.
    op.create_index(
        "ix_artifacts_approval_status",
        "artifacts",
        ["approval_status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_artifacts_approval_status", table_name="artifacts")
    op.drop_column("artifacts", "rejection_reason")
    op.drop_column("artifacts", "approved_at")
    op.drop_column("artifacts", "approved_by")
    op.drop_column("artifacts", "approval_status")
