"""Add organizations.suspended boolean column.

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-12

organizations is GLOBAL (non-RLS). suspended=False by default; existing rows
get false via server_default so the NOT NULL constraint is satisfied immediately.
"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("suspended", sa.Boolean(), nullable=False, server_default=sa.false()))
    print("\n[0014] organizations.suspended column added (default false).")


def downgrade() -> None:
    op.drop_column("organizations", "suspended")
