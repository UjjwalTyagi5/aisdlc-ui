"""0016 run offering identity + unique provider connection name

Phase 2 of the BYOK model-identity fix:
  - runs.offering_id          — the EXACT offering (provider connection + model) a
                                run was dispatched against; removes the silent
                                first-match when two keys expose the same model_id.
  - runs.model_display_name   — audit snapshot of the connection name at dispatch.
  - uq_model_provider_name_per_tenant — case-insensitive unique connection name per
                                tenant so a selected model is always identifiable.

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("offering_id", sa.String(36), nullable=True))
    op.add_column("runs", sa.Column("model_display_name", sa.String(255), nullable=True))

    # Heal any pre-existing duplicate connection names within a tenant before
    # enforcing uniqueness: keep the earliest, suffix the rest with a short id so
    # the rename is deterministic and itself collision-free.
    op.execute(
        """
        WITH ranked AS (
            SELECT id, tenant_id, display_name,
                   row_number() OVER (
                       PARTITION BY tenant_id, lower(display_name)
                       ORDER BY created_at, id
                   ) AS rn
            FROM model_providers
        )
        UPDATE model_providers p
        SET display_name = left(p.display_name, 240) || ' (' || left(r.id::text, 8) || ')'
        FROM ranked r
        WHERE p.id = r.id AND r.rn > 1
        """
    )

    # Case-insensitive uniqueness of provider connection names within a tenant.
    op.execute(
        "CREATE UNIQUE INDEX uq_model_provider_name_per_tenant "
        "ON model_providers (tenant_id, lower(display_name))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_model_provider_name_per_tenant")
    op.drop_column("runs", "model_display_name")
    op.drop_column("runs", "offering_id")
