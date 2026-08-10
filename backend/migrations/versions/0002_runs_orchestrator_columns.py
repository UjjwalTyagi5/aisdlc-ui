"""Add current_stage and gate_pending to runs table for orchestrator state tracking.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-28

current_stage: nullable VARCHAR(50) — orchestrator records which SDLC stage is active (requirements,
design, development, testing). Nullable so existing rows are unaffected without a data migration.

gate_pending: non-null BOOLEAN DEFAULT false — flags that a human approval gate is awaiting
resolution before the orchestrator advances to the next stage. server_default="false" ensures
existing rows receive a safe default without a data migration.
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("current_stage", sa.String(50), nullable=True))
    op.add_column("runs", sa.Column("gate_pending", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    op.drop_column("runs", "gate_pending")
    op.drop_column("runs", "current_stage")
