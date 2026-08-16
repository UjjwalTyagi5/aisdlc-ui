"""0036 model_providers.secret_ref nullable — keyless onboarding.

Task 3 of docs/superpowers/plans/2026-08-11-model-gateway-bu-cascade.md adds keyless
provider onboarding (`api_key` optional, surfaced as `secret_ref: None`) so a connection's
models can be granted centrally while a Business Unit/project supplies its own key later
(spec §2.3). `model_providers.secret_ref` was NOT NULL since 0015_model_provider — this
migration was missing from Task 1's 0035_model_grants_cascade and is added here so the
column can actually hold NULL.

Revision ID: 0036
Revises: 0035
"""
import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("model_providers", "secret_ref", existing_type=sa.String(255), nullable=True)


def downgrade() -> None:
    # NOTE: any rows with secret_ref IS NULL (keyless connections) must be backfilled
    # or removed before downgrading, or this will fail with a NOT NULL violation.
    op.alter_column("model_providers", "secret_ref", existing_type=sa.String(255), nullable=False)
