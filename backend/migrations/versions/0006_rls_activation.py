"""RLS activation: LEGACY_TENANT backfill, NOT NULL enforce, FORCE RLS, composite indexes.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-06

Backfills NULL tenant_id rows to LEGACY_TENANT_ID, enforces NOT NULL, activates
FORCE ROW LEVEL SECURITY on all 5 tenant-scoped tables, adds composite (tenant_id,
created_at DESC) indexes on high-volume tables and (tenant_id, run_id) on artifacts
for performance (M7 SC-05).

Must run as superuser (POSTGRES_MIGRATIONS_CONN_STRING). The app role is subject
to the RLS policies after this migration completes — never use POSTGRES_CONN_STRING
for migrations. The superuser requirement is enforced in migrations/env.py.

IMPORTANT: 0001_initial_schema.py already issued:
  ALTER TABLE {t} ENABLE ROW LEVEL SECURITY
  CREATE POLICY tenant_isolation ON {t} USING (...)
  CREATE POLICY tenant_isolation_insert ON {t} WITH CHECK (...)
This migration adds ONLY FORCE ROW LEVEL SECURITY — it does NOT re-ENABLE RLS and
does NOT re-author policies. Re-authoring would raise "policy already exists" errors.
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# Well-known UUID for all pre-tenancy rows. Must match the seed Organization.id
# inserted in Step 0. Never generate per-row (D-01 — must be a single canonical value).
LEGACY_TENANT_ID = "00000000-0000-0000-0000-000000000001"

# The five tenant-scoped tables confirmed from 0001_initial_schema.py.
# These are the only tables with tenant_id columns; matches _RLS_TABLES in 0001.
_RLS_TABLES = ("projects", "runs", "artifacts", "audit_events", "agent_call_logs")


def upgrade() -> None:
    bind = op.get_bind()

    # Step 0: Seed the legacy organization so LEGACY_TENANT_ID has a valid org row.
    # ON CONFLICT DO NOTHING makes this idempotent on re-run (D-01, D-03).
    op.execute(
        f"""
        INSERT INTO organizations (id, slug, display_name, created_at, updated_at)
        VALUES (
            '{LEGACY_TENANT_ID}',
            'legacy',
            'Legacy (pre-tenancy)',
            now(),
            now()
        )
        ON CONFLICT (id) DO NOTHING
        """
    )

    # Step 1: Dry-run — log per-table NULL counts so the operator can review before
    # committing. This is the D-02 verification gate: counts are visible in migration
    # output, and the operator can abort if they look wrong.
    print("\n[0006] Pre-backfill NULL tenant_id counts (dry-run):")
    for table in _RLS_TABLES:
        row = bind.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE tenant_id IS NULL")
        ).fetchone()
        count = row[0] if row else 0
        print(f"  {table}: {count} row(s) with NULL tenant_id will be backfilled")

    # Step 2: Backfill — NULL values only.
    # Existing non-NULL rows are NEVER overwritten regardless of their tenant_id value
    # (WHERE tenant_id IS NULL guard — Pitfall 3 / T-M7.1-11 mitigation).
    for table in _RLS_TABLES:
        op.execute(
            sa.text(
                f"UPDATE {table} SET tenant_id = '{LEGACY_TENANT_ID}' WHERE tenant_id IS NULL"
            )
        )

    # Step 3: Post-backfill assertion — must reach 0 NULL rows before constraining.
    # Fail loudly rather than apply NOT NULL on a table that still has NULLs (D-02).
    print("\n[0006] Post-backfill NULL tenant_id counts (should all be 0):")
    for table in _RLS_TABLES:
        row = bind.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE tenant_id IS NULL")
        ).fetchone()
        remaining = row[0] if row else 0
        print(f"  {table}: {remaining} NULL row(s) remaining")
        if remaining != 0:
            raise RuntimeError(
                f"[0006] Backfill incomplete: {table} still has {remaining} row(s) with "
                "NULL tenant_id after UPDATE. Cannot enforce NOT NULL. "
                "Investigate and re-run — do not skip this check."
            )

    # Step 4: Enforce NOT NULL — only after backfill is verified.
    # The ORM already declares tenant_id nullable=False; this makes it explicit in
    # the DB schema for any table where the constraint may have drifted (D-02 gate).
    for table in _RLS_TABLES:
        op.alter_column(table, "tenant_id", nullable=False)

    # Step 5: FORCE ROW LEVEL SECURITY on all 5 tables (D-08).
    # 0001 already issued ALTER TABLE ... ENABLE ROW LEVEL SECURITY and
    # CREATE POLICY tenant_isolation / tenant_isolation_insert — do NOT repeat those.
    # FORCE makes even the table-owner role subject to the policies.
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # Step 6: Composite indexes for RLS query performance (M7 SC-05, Pitfall 6).
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction — Alembic wraps the
    # migration body in a transaction by default. `autocommit_block()` is Alembic's
    # canonical mechanism: it COMMITs the migration's open transaction and switches
    # the connection to autocommit for the duration of the block, then restores a
    # transaction afterward. (A bare `bind.execution_options(isolation_level="AUTOCOMMIT")`
    # fails with "isolation_level may not be altered" because the connection is already
    # mid-transaction from the steps above.) The backfill/NOT NULL/FORCE steps above
    # already ran and are committed by autocommit_block's entry COMMIT.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_audit_tenant_created "
            "ON audit_events (tenant_id, created_at DESC)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_acl_tenant_created "
            "ON agent_call_logs (tenant_id, created_at DESC)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_artifact_tenant_run "
            "ON artifacts (tenant_id, run_id)"
        )
    print("\n[0006] Composite indexes created (CONCURRENTLY — no table lock).")


def downgrade() -> None:
    # Step 1: Drop composite indexes (CONCURRENTLY — no lock; same autocommit_block
    # mechanism as upgrade because DROP INDEX CONCURRENTLY also cannot run inside a
    # transaction).
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_artifact_tenant_run")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_acl_tenant_created")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_audit_tenant_created")

    # Step 2: Remove FORCE — revert to ENABLE-only (the state 0001 left the tables in).
    # This is NOT ALTER TABLE ... DISABLE ROW LEVEL SECURITY — RLS itself remains active;
    # only the FORCE flag is removed. The tenant_isolation policies from 0001 are untouched.
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

    # Step 3: Reverse the NOT NULL constraint — allows NULL tenant_id again
    # (needed before the backfill reversal below sets rows back to NULL).
    for table in _RLS_TABLES:
        op.alter_column(table, "tenant_id", nullable=True)

    # Step 4: Reverse the backfill — set legacy rows back to NULL.
    # Only rows that were assigned LEGACY_TENANT_ID by this migration are touched.
    # Any row whose tenant_id happened to equal LEGACY_TENANT_ID before this migration
    # ran will also be NULLed — this is acceptable given D-03 (reversibility over
    # perfect precision; the legacy org can be reassigned later).
    # The seeded legacy organization row is intentionally left in place (D-03 reversibility
    # principle — deleting it could break FK references in tables not listed here).
    for table in _RLS_TABLES:
        op.execute(
            sa.text(
                f"UPDATE {table} SET tenant_id = NULL "
                f"WHERE tenant_id = '{LEGACY_TENANT_ID}'"
            )
        )
