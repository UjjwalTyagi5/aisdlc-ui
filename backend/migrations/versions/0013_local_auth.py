"""Local email+password auth — users.password_hash + platform_users table.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-11

users + platform_users are GLOBAL (non-RLS): login lookup precedes tenant context
(D-08). No RLS lifecycle here. password_hash nullable (existing rows have none).
"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(255), nullable=True))
    op.create_table(
        "platform_users",
        sa.Column("user_id", sa.String(255), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("platform_role", sa.String(32), nullable=False, server_default="platform_admin"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("email", name="uq_platform_users_email"),
    )
    print("\n[0013] users.password_hash added; platform_users table created (global, non-RLS).")


def downgrade() -> None:
    op.drop_table("platform_users")
    op.drop_column("users", "password_hash")
