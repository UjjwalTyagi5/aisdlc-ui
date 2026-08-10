"""Add temporal_workflow_id and temporal_run_id to runs table for Temporal integration.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-02

temporal_workflow_id: nullable VARCHAR(255) with index — the Temporal workflow ID
(e.g. "sdlc-run-<uuid>") written back to the runs row when a workflow is started via
client.start_workflow(). Indexed for signal-dispatch lookups (D-07, plan-07 prerequisite).

temporal_run_id: nullable VARCHAR(255) — the first-execution run ID returned by
handle.first_execution_run_id; used for Temporal Web UI deep-links.

Both columns are nullable so existing rows are unaffected without a data migration.
Downgrade drops them in reverse order to satisfy any column dependencies.
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("temporal_workflow_id", sa.String(255), nullable=True))
    op.create_index("ix_runs_temporal_workflow_id", "runs", ["temporal_workflow_id"])
    op.add_column("runs", sa.Column("temporal_run_id", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "temporal_run_id")
    op.drop_index("ix_runs_temporal_workflow_id", table_name="runs")
    op.drop_column("runs", "temporal_workflow_id")
