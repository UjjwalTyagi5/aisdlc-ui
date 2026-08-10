"""budgets — hierarchical monthly cost limits (org → workspace → project).

Revision ID: 0032_budgets
Revises: 0031_merge_model_limits_pipeline

Adds a DB-backed monthly USD budget at each level of the tenancy hierarchy and a
durable per-scope monthly spend rollup that backs both reporting and enforcement.

1. `monthly_budget_usd Numeric(12,2) NULL` on organizations, workspaces, projects.
   NULL means "no budget" (inherit the parent / unlimited) — existing rows unaffected.

2. New `usage_monthly` table — the authoritative, durable monthly spend rollup.
   One row per (tenant_id, scope, scope_id, month); UPSERT-ed by the usage meter on
   every LLM completion. Survives a Redis flush and seeds the hot Redis counters.
   Tenant-private → full RLS lifecycle (ENABLE + CREATE POLICY USING/WITH CHECK +
   FORCE), exactly like 0010/eval_records (Pitfall 3 — a NEW table needs the full
   lifecycle, not 0006's FORCE-only loop). Policy expression matches 0001 verbatim so
   RLS coverage tooling reads uniformly.

Must run as superuser (POSTGRES_MIGRATIONS_CONN_STRING) — usage_monthly has FORCE RLS.
"""
import uuid as _uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0032_budgets"
down_revision = "0031_merge_model_limits_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Step 1: budget columns (NULL = no budget) ─────────────────────────────
    for table in ("organizations", "workspaces", "projects"):
        op.add_column(table, sa.Column("monthly_budget_usd", sa.Numeric(12, 2), nullable=True))

    # ── Step 2: usage_monthly durable rollup ──────────────────────────────────
    op.create_table(
        "usage_monthly",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4),
        # RLS anchor: no FK intentional — tenant_id is a policy column (mirrors
        # Project.tenant_id / agent_call_logs.tenant_id).
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False, index=True),
        # 'org' | 'workspace' | 'project'
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("scope_id", UUID(as_uuid=True), nullable=False),
        # calendar month, 'YYYYMM' (UTC) — matches the meter's Redis cost key
        sa.Column("month", sa.String(6), nullable=False),
        # Numeric(14,6) — exact decimal for financial aggregates (Float drifts).
        sa.Column("cost_usd", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id", "scope", "scope_id", "month", name="uq_usage_monthly_scope_month"
        ),
    )

    # ── Step 3: full RLS lifecycle (new table — see 0010 docstring) ───────────
    op.execute("ALTER TABLE usage_monthly ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON usage_monthly "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON usage_monthly "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute("ALTER TABLE usage_monthly FORCE ROW LEVEL SECURITY")

    print(
        "\n[0032] budgets: monthly_budget_usd added to organizations/workspaces/projects; "
        "usage_monthly rollup created with full RLS (ENABLE + POLICY + FORCE)."
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON usage_monthly")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON usage_monthly")
    op.drop_table("usage_monthly")
    for table in ("projects", "workspaces", "organizations"):
        op.drop_column(table, "monthly_budget_usd")
