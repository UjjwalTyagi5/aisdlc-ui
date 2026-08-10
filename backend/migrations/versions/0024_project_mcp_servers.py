"""0024 add projects.mcp_servers column

Per-project stage→MCP-server mapping: {agent_id: [mcp_server_id, ...]}. Chosen at
project creation from the creator's registered MCP servers; threaded into each run
(SDLCWorkflowInput.mcp_servers) and consumed by the matching agent activity.

No RLS changes — projects already has FORCE RLS (0001).

Revision ID: 0024
Revises: 0023
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("mcp_servers", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "mcp_servers")
