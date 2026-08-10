"""0030 pipeline artifacts — deployment + documentation artifact columns

Revision ID: 0030_pipeline_artifacts
Revises: 0029_conversation_agent_list, 0029_dev_workspaces
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0030_pipeline_artifacts"
down_revision = ("0029_conversation_agent_list", "0029_dev_workspaces")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("deployment_artifacts", JSONB(), nullable=True))
    op.add_column("runs", sa.Column("documentation_artifacts", JSONB(), nullable=True))
    op.add_column("agent_sessions", sa.Column("documentation_artifacts", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_sessions", "documentation_artifacts")
    op.drop_column("runs", "documentation_artifacts")
    op.drop_column("runs", "deployment_artifacts")
