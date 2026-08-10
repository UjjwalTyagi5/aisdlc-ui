"""0022 add security_artifacts column

Adds a JSONB column to the runs table for storing Security Agent output
(SecurityArtifact model_dump()). Follows the same pattern as existing
artifact columns (requirements_payload, design_artifacts, etc.).

No RLS changes — runs table already has FORCE RLS (0001).

Revision ID: 0022
Revises: 0021
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("security_artifacts", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "security_artifacts")
