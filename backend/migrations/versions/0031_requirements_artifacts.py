"""0031 add requirements_artifacts column

Adds a JSONB column to the runs table for storing Requirements Agent panel
output (normalised Gherkin AC sections + generated-docx file-tree flag).
Follows the same pattern as existing artifact columns (requirements_payload,
design_artifacts, code_review_artifacts, etc.).

No RLS changes — runs table already has FORCE RLS (0001).

Revision ID: 0031
Revises: 0030_pipeline_artifacts
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0031"
down_revision = "0030_pipeline_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("requirements_artifacts", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "requirements_artifacts")
