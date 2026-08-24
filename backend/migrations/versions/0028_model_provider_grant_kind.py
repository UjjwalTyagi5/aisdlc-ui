"""Allow integration_grants to carry model-provider grants.

Reuses the existing integration_grants table (0015_integration_grants) rather than
adding a parallel one — same reasoning as that migration's own docstring: "the same
decision made about two kinds of thing." A model_provider grant means "this Business
Unit may use provider X" — no model, no key, exactly like a connector grant.

Revision ID: 0028_model_provider_grant_kind
Revises: 0027_merge_heads_3
"""
from alembic import op

revision = "0028_model_provider_grant_kind"
down_revision = "0027_merge_heads_3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE integration_grants DROP CONSTRAINT ck_integration_grant_kind")
    op.execute(
        "ALTER TABLE integration_grants ADD CONSTRAINT ck_integration_grant_kind "
        "CHECK (kind IN ('connector', 'mcp', 'model_provider'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE integration_grants DROP CONSTRAINT ck_integration_grant_kind")
    op.execute(
        "ALTER TABLE integration_grants ADD CONSTRAINT ck_integration_grant_kind "
        "CHECK (kind IN ('connector', 'mcp'))"
    )
