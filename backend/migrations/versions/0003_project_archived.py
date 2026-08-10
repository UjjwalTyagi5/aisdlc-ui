"""Add archived boolean column to projects table for archive/restore workflow.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-31

archived: non-null BOOLEAN DEFAULT false — flags that a project has been
archived by a user action (POST /projects/{id}/archive). server_default="false"
ensures all existing rows receive a safe default without a data migration.
The restore endpoint (POST /projects/{id}/restore) flips this back to false.
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "archived")
