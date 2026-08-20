"""Add projects.track — the delivery track chosen once at project creation.

Revision ID: 0024_project_track
Revises: 0023_merge_heads
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_project_track"
down_revision = "0023_merge_heads"
branch_labels = None
depends_on = None

_TRACKS = "('greenfield','enhancement','modernization','rpa_infra','data_engineering')"


def upgrade() -> None:
    op.add_column("projects", sa.Column("track", sa.String(length=20), nullable=True))
    op.create_check_constraint(
        "ck_project_track", "projects", f"track IN {_TRACKS}"
    )


def downgrade() -> None:
    op.drop_constraint("ck_project_track", "projects", type_="check")
    op.drop_column("projects", "track")
