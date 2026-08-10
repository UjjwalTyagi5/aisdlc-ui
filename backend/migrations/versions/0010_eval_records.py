"""Eval harness — eval_records table + full RLS lifecycle (REQ-M9-10).

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-10

Creates the `eval_records` table for the M9.3 eval harness — a per-run quality
signal log written by the eval emission path (plan 02) and read by the eval API
(plan 03).

eval_records is tenant-private and RLS-scoped (D-M9-02). This is a NEW table —
0001 never defined it — so this migration must issue the FULL RLS lifecycle,
exactly like 0007 did for user_workspace_roles:

  ALTER TABLE eval_records ENABLE ROW LEVEL SECURITY
  CREATE POLICY tenant_isolation ... USING (current_setting(..., true)::uuid)
  CREATE POLICY tenant_isolation_insert ... WITH CHECK (current_setting(..., true)::uuid)
  ALTER TABLE eval_records FORCE ROW LEVEL SECURITY

Do NOT copy 0006's FORCE-only loop — that loop assumes ENABLE + CREATE POLICY
already exist from 0001 (Pitfall 3 from 0007/M7.2). Omitting ENABLE + CREATE
POLICY here would leave eval_records wide-open despite FORCE.

The policy expression matches 0001/0007's shape verbatim so RLS verification
tooling (test_rls_coverage.py) reads uniformly across all _RLS_TABLES.

A composite index ix_eval_tenant_run_created (tenant_id, run_id, created_at DESC)
supports the run-scoped read query (plan 03). CREATE INDEX CONCURRENTLY cannot
run inside a transaction — wrapped in its own op.get_context().autocommit_block()
per the M7.1 live-bug pattern (0008 lines 69-74).

Must run as superuser (POSTGRES_MIGRATIONS_CONN_STRING) — same requirement as
0001/0006/0007/0009 because eval_records has FORCE ROW LEVEL SECURITY.
"""
import uuid as _uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Step 1: Create eval_records table — mirrors EvalRecord ORM
    # (shared/models/orm.py) column-for-column.
    # ------------------------------------------------------------------
    op.create_table(
        "eval_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4),
        # RLS anchor: no FK intentional — tenant_id is a policy column, not a
        # relational FK (mirrors Project.tenant_id / user_workspace_roles.tenant_id).
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False, index=True),
        # run_id stored as string — matches AgentCallLog convention (Run.id is a
        # string in this codebase).
        sa.Column("run_id", sa.String(255), nullable=True, index=True),
        sa.Column("agent_type", sa.String(50), nullable=False),
        # Numeric(5,4) — exact decimal for a 0.0000-1.0000 quality score; Float
        # would introduce rounding drift.
        sa.Column("score", sa.Numeric(5, 4), nullable=True),
        sa.Column("signals", JSONB, nullable=True),
        # No updated_at — append-only quality log, immutable after insert.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            index=True,
        ),
    )

    # ------------------------------------------------------------------
    # Step 2: Full RLS lifecycle for eval_records (D-M9-02, T-9.3-01/02).
    #
    # This table is NEW — 0001 never touched it — so ENABLE + CREATE POLICY
    # + FORCE are all needed here, exactly like 0007's user_workspace_roles
    # (Pitfall 3). Do NOT reuse 0006's FORCE-only loop, which assumes
    # ENABLE + CREATE POLICY already exist for the original 5 tables.
    #
    # Policy expression matches 0001/0007 exactly:
    #   current_setting('app.current_tenant_id', true)::uuid
    # The `true` flag makes current_setting return NULL when unset rather than
    # raising an error — the cast to ::uuid then yields NULL so the USING clause
    # evaluates to false, blocking all rows when no tenant GUC is set (safe default).
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE eval_records ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON eval_records "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON eval_records "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute("ALTER TABLE eval_records FORCE ROW LEVEL SECURITY")

    # ------------------------------------------------------------------
    # Step 3: Composite index for run-scoped reads (plan 03).
    #
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction — Alembic wraps
    # the migration body in a transaction by default. autocommit_block() is
    # Alembic's documented escape hatch (M7.1 live-bug pattern, 0008 lines 69-74).
    # ------------------------------------------------------------------
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_eval_tenant_run_created "
            "ON eval_records (tenant_id, run_id, created_at DESC)"
        )

    print(
        "\n[0010] eval_records created: ENABLE + CREATE POLICY (USING + WITH CHECK) "
        "+ FORCE ROW LEVEL SECURITY applied. "
        "Composite index ix_eval_tenant_run_created created (CONCURRENTLY)."
    )


def downgrade() -> None:
    # Drop the composite index (CONCURRENTLY — no table lock).
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_eval_tenant_run_created")

    # Drop the RLS policies added in upgrade() before dropping the table.
    # DROP POLICY IF EXISTS is safe if upgrade() was partially applied.
    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON eval_records")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON eval_records")

    # Table drop removes its remaining indexes (FK dependency order N/A — no FKs).
    op.drop_table("eval_records")
