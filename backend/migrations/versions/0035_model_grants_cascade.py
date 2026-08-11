"""0035 model grants cascade — org_model_grants, project_model_selections,
model_providers approval columns.

Implements the data model from docs/superpowers/specs/2026-08-11-model-gateway-bu-cascade-design.md §2.

org_model_grants: the only place a model enters the org's catalogue for use beyond its
onboarding provider. A `global` grant reaches every BU automatically; `specific` reaches
only the named units (business_unit_ids). Same model can be granted twice under two
different keys (credential_id), so uniqueness is (tenant_id, provider, model_id,
credential_id) — with a partial index covering the NULL-credential ("any key") case,
since Postgres treats every NULL as distinct in a normal unique constraint.

project_model_selections: what one project actually uses. No row (or an empty `selected`)
means "inherit the BU's full allowed set" — enforced in the service layer, not here.

model_providers gains approval workflow columns (schema only in this plan — see spec §8
known gap 2 for why the workflow itself isn't reachable yet).

Both new tables are tenant-private → full RLS lifecycle (ENABLE + POLICY + FORCE), same
pattern as 0032/usage_monthly and 0015/model_providers.

Revision ID: 0035
Revises: 0034_merge_skills_budgets
"""
import uuid as _uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0035"
down_revision = "0034_merge_skills_budgets"
branch_labels = None
depends_on = None


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
    # ── org_model_grants ───────────────────────────────────────────────────
    op.create_table(
        "org_model_grants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column(
            "credential_id", UUID(as_uuid=True),
            sa.ForeignKey("model_providers.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("visibility", sa.String(16), nullable=False, server_default="global"),
        sa.Column("business_unit_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_org_model_grants_tenant_id", "org_model_grants", ["tenant_id"])
    # Non-null credential_id: normal unique constraint.
    op.create_unique_constraint(
        "uq_org_grant_cred", "org_model_grants", ["tenant_id", "provider", "model_id", "credential_id"],
    )
    # Null credential_id ("any key"): partial unique index, since NULL <> NULL would
    # otherwise let duplicate "any key" grants for the same model slip past the constraint
    # above.
    op.execute(
        "CREATE UNIQUE INDEX uq_org_grant_null_cred ON org_model_grants "
        "(tenant_id, provider, model_id) WHERE credential_id IS NULL"
    )
    _force_rls("org_model_grants")

    # ── project_model_selections ───────────────────────────────────────────
    op.create_table(
        "project_model_selections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "project_id", UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True,
        ),
        sa.Column("selected", JSONB, nullable=False, server_default="[]"),
        sa.Column("default_key", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_project_model_selections_tenant_id", "project_model_selections", ["tenant_id"])
    _force_rls("project_model_selections")

    # ── model_providers approval workflow columns ──────────────────────────
    op.add_column("model_providers", sa.Column("approval_status", sa.String(20), nullable=False, server_default="active"))
    op.add_column("model_providers", sa.Column("approval_decided_by", sa.String(255), nullable=True))
    op.add_column("model_providers", sa.Column("approval_decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("model_providers", sa.Column("approval_reason", sa.Text(), nullable=True))

    print(
        "\n[0035] org_model_grants + project_model_selections created (full RLS); "
        "model_providers.approval_status/decided_by/decided_at/reason added."
    )


def downgrade() -> None:
    op.drop_column("model_providers", "approval_reason")
    op.drop_column("model_providers", "approval_decided_at")
    op.drop_column("model_providers", "approval_decided_by")
    op.drop_column("model_providers", "approval_status")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON project_model_selections")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON project_model_selections")
    op.drop_table("project_model_selections")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON org_model_grants")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON org_model_grants")
    op.execute("DROP INDEX IF EXISTS uq_org_grant_null_cred")
    op.drop_table("org_model_grants")
