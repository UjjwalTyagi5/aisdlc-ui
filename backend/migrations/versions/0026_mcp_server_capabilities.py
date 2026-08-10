"""mcp_servers.capabilities — admin-asserted BYO capability tags (DP2)

Revision ID: 0026_mcp_server_capabilities
Revises: 0025
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0026_mcp_server_capabilities"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mcp_servers", sa.Column("capabilities", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("mcp_servers", "capabilities")
