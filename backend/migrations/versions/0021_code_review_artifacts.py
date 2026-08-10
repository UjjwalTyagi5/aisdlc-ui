"""0021 add code_review_artifacts column

Adds a JSONB column to the runs table for storing Code Review Agent output
(CodeReviewArtifact model_dump()). Follows the same pattern as existing
artifact columns (requirements_payload, design_artifacts, etc.).

No RLS changes — runs table already has FORCE RLS (0001).

Revision ID: 0021
Revises: 0020
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("code_review_artifacts", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "code_review_artifacts")
