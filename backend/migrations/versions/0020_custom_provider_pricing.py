"""0020 dynamic/custom provider onboarding

Adds the columns needed to onboard ANY LiteLLM-supported provider via a custom
API key, governed by the existing model:manage RBAC:

- model_providers.api_base       — optional custom endpoint (OpenAI-compatible /
                                    self-hosted / gateway base URL). NULL for the
                                    provider's default endpoint.
- model_providers.is_custom      — true when the provider is not one of the
                                    curated catalog presets (anthropic/openai/google).
- model_offerings.input_price_per_million / output_price_per_million
                                    — USD per 1M tokens. Mandatory for custom
                                    models so Cost/Langfuse can attribute spend
                                    (single-source-of-truth: pricing rides on the
                                    model). NULL for catalog models (priced from
                                    LiteLLM's cost map at read time).

No table creation, no RLS changes — these tables already FORCE RLS (0015).

Revision ID: 0020
Revises: 0019
"""
import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_providers", sa.Column("api_base", sa.String(512), nullable=True))
    op.add_column(
        "model_providers",
        sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "model_offerings", sa.Column("input_price_per_million", sa.Numeric(12, 4), nullable=True)
    )
    op.add_column(
        "model_offerings", sa.Column("output_price_per_million", sa.Numeric(12, 4), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("model_offerings", "output_price_per_million")
    op.drop_column("model_offerings", "input_price_per_million")
    op.drop_column("model_providers", "is_custom")
    op.drop_column("model_providers", "api_base")
