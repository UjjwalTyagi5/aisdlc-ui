"""0015 model provider (BYOK) foundation

Three FORCE-RLS tables (model_providers, model_offerings, app_secrets), a
runs.model_id column, and the model:manage permission seed. RLS policy
expression matches 0007/0012 exactly: current_setting('app.current_tenant_id', true)::uuid.

Revision ID: 0015
Revises: 0014
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_RLS_TABLES = ("model_providers", "model_offerings", "app_secrets")


def _force_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        f"CREATE POLICY tenant_isolation_insert ON {table} "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.create_table(
        "model_providers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("secret_ref", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="unverified"),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_model_providers_tenant_id", "model_providers", ["tenant_id"])

    op.create_table(
        "model_offerings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["provider_id"], ["model_providers.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("provider_id", "model_id", name="uq_offering_provider_model"),
    )
    op.create_index("ix_model_offerings_tenant_id", "model_offerings", ["tenant_id"])
    op.create_index("ix_model_offerings_provider_id", "model_offerings", ["provider_id"])
    # At most one default offering per tenant (partial unique index).
    op.execute(
        "CREATE UNIQUE INDEX uq_one_default_per_tenant ON model_offerings (tenant_id) "
        "WHERE is_default"
    )

    op.create_table(
        "app_secrets",
        sa.Column("tenant_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("ref", sa.String(255), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    for t in _RLS_TABLES:
        _force_rls(t)

    op.add_column("runs", sa.Column("model_id", sa.String(100), nullable=True))

    # Seed the model:manage permission so the catalog table stays in parity with
    # ALL_PERMISSIONS (drift guard). Idempotent. permissions table has only name (PK).
    op.execute(
        "INSERT INTO permissions (name) VALUES ('model:manage') ON CONFLICT (name) DO NOTHING"
    )

    print(
        "\n[0015] model_providers + model_offerings + app_secrets (FORCE RLS) created; "
        "runs.model_id added; model:manage seeded into permissions."
    )


def downgrade() -> None:
    op.execute("DELETE FROM permissions WHERE name = 'model:manage'")
    op.drop_column("runs", "model_id")
    for t in _RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_insert ON {t}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
    op.drop_table("app_secrets")
    op.execute("DROP INDEX IF EXISTS uq_one_default_per_tenant")
    op.drop_table("model_offerings")
    op.drop_table("model_providers")
