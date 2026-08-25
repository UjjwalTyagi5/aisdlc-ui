"""Add project_integration_credentials.label.

The frontend's ProjectIntegrationCredential/-Input (frontend/lib/schemas/
project-integration.ts) always carried a required `label` — "Payments CI bot",
the team's own name for the credential — but the table had nowhere to put it.
Nullable: existing rows (if any) predate the field and read as an empty label
rather than fail to parse.

Revision ID: 0030_credential_label
Revises: 0029_notification_kind
"""
from alembic import op
import sqlalchemy as sa

revision = "0030_credential_label"
down_revision = "0029_notification_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_integration_credentials",
        sa.Column("label", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_integration_credentials", "label")
