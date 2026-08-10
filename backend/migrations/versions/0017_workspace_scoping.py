"""0017 workspace scoping — enrich workspaces + model_providers.workspace_id

Makes workspace a real, self-describing boundary:
  - workspaces gains business_unit, cost_center, data_classification, status so a
    workspace describes its business unit + lifecycle (multiple per org now).
  - model_providers gains a nullable workspace_id (NULL = org-shared; a value =
    workspace-owned). Selection/visibility is scoped by it; run dispatch is
    unaffected (resolves by offering_id).

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspaces", sa.Column("business_unit", sa.String(120), nullable=True))
    op.add_column("workspaces", sa.Column("cost_center", sa.String(64), nullable=True))
    op.add_column(
        "workspaces",
        sa.Column("data_classification", sa.String(20), nullable=False, server_default="internal"),
    )
    op.add_column(
        "workspaces",
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
    )

    op.add_column("model_providers", sa.Column("workspace_id", UUID(as_uuid=True), nullable=True))
    op.create_index(
        "ix_model_providers_workspace_id", "model_providers", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_model_providers_workspace_id", table_name="model_providers")
    op.drop_column("model_providers", "workspace_id")
    op.drop_column("workspaces", "status")
    op.drop_column("workspaces", "data_classification")
    op.drop_column("workspaces", "cost_center")
    op.drop_column("workspaces", "business_unit")
