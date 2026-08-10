"""Add trigger column to runs and make project_id nullable for webhook-triggered runs.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-05

trigger: NOT NULL VARCHAR(50) server_default 'manual'. Records how the run was
initiated ('manual' via POST /runs, 'webhook' via an inbound provider delivery).
Surfaced by RunOut so GET /runs can distinguish webhook-triggered pipelines
(M6 SC-06). server_default backfills existing rows as 'manual'.

project_id: relaxed to nullable. Webhook-triggered runs are not bound to a
pre-existing Postgres `projects` row — the inbound event carries the provider's
project key, not a local project UUID. Manual runs still set project_id.

Downgrade reverses both. The project_id NOT NULL restore will fail if any
webhook run with NULL project_id exists; delete those first if downgrading.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("trigger", sa.String(50), nullable=False, server_default="manual"),
    )
    op.alter_column(
        "runs",
        "project_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "runs",
        "project_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.drop_column("runs", "trigger")
