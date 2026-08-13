"""Drop the Temporal workflow ids from `runs`.

Temporal is gone. It was an optional orchestration engine (`ENABLE_TEMPORAL`), disabled
in every environment, and the only endpoint that started a workflow answered 503 the
whole time it was off — so what these columns recorded was a run shape nothing has
produced. Every run is conversational now: the Copilot drives each stage and gate
approvals advance it.

The columns are dropped rather than left nullable. A column that can only ever be NULL
still shows up in every SELECT * and every model, and reads as "this is populated
sometimes" to whoever meets it next — which is the thing that makes dead schema
expensive rather than merely untidy.

IRREVERSIBLE IN PRACTICE, and the downgrade says so honestly: it re-adds the columns so
the schema matches, but the ids themselves are not recoverable. Nothing depended on
them, which is the reason this is safe rather than the reason it is reversible.

Revision ID: 0012_drop_temporal_columns
Revises: 0011_role_permission_overrides
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_drop_temporal_columns"
down_revision = "0011_role_permission_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The index came with temporal_workflow_id in 0004 and has no other user.
    op.drop_index("ix_runs_temporal_workflow_id", table_name="runs", if_exists=True)
    op.drop_column("runs", "temporal_workflow_id")
    op.drop_column("runs", "temporal_run_id")


def downgrade() -> None:
    op.add_column("runs", sa.Column("temporal_run_id", sa.String(length=255), nullable=True))
    op.add_column("runs", sa.Column("temporal_workflow_id", sa.String(length=255), nullable=True))
    op.create_index("ix_runs_temporal_workflow_id", "runs", ["temporal_workflow_id"])
