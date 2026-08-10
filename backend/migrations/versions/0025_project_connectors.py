"""0025 add projects.connectors column

Per-project stage→connector mapping {agent_id: [connector_kind, ...]}. Chosen at
project creation alongside the MCP mapping; threaded into each run and used by the
matching agent activity to inject the stage's connector (falls back to the tenant's
azure_devops default when a stage has no selection — backward compatible).

No RLS changes — projects already has FORCE RLS (0001).

Revision ID: 0025
Revises: 0024
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("connectors", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "connectors")
