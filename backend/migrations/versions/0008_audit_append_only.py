"""Audit append-only enforcement: REVOKE UPDATE/DELETE + composite query index.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-09

Implements REQ-M8-01/02 (append-only DB enforcement) and REQ-M8-08 (< 200ms query at
10M rows):

  1. REVOKE UPDATE, DELETE ON audit_events FROM sdlc_app — permanent DB-level control.
     After this migration the restricted app role can only INSERT into audit_events.
     No application code can circumvent this; superuser-only GRANT-back in production.

  2. CREATE INDEX CONCURRENTLY ix_audit_tenant_run_created ON audit_events
     (tenant_id, resource_id, created_at DESC) — composite index for run-scoped queries.
     resource_id stores run_id per the existing signals.py pattern (RESEARCH Open Question 1).
     This is ADDITIVE to the existing M7.1 ix_audit_tenant_created (tenant_id, created_at).

IMPORTANT: Both REVOKE and CREATE INDEX CONCURRENTLY MUST each run inside a SEPARATE
`op.get_context().autocommit_block()`. This is the M7.1-proven fix for the live bug where
a bare op.execute() failed mid-transaction (STATE.md — Pitfall 2 in RESEARCH.md).
CREATE INDEX CONCURRENTLY cannot run inside a transaction; REVOKE fails similarly in some
PG configurations inside a transaction block.

Must run as superuser (POSTGRES_MIGRATIONS_CONN_STRING). The app role (sdlc_app) is not
the migration executor and is unaffected in that role — the REVOKE targets sdlc_app's DML
privileges on the table, not the migration runner.

Downgrade: drops the index (safe, CONCURRENTLY, no lock). The REVOKE cannot be meaningfully
undone without a superuser GRANT-back — documented by comment only; no automatic rollback
in production (D-M8-04 locked decision).
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: REVOKE UPDATE + DELETE on audit_events from the restricted app role.
    #
    # This makes audit_events append-only for sdlc_app: INSERTs still succeed;
    # any UPDATE or DELETE attempt raises "permission denied" (REQ-M8-01/02).
    #
    # Must run in autocommit_block() — GRANT/REVOKE can behave unexpectedly inside
    # an open transaction on some Postgres configurations (same root cause as the
    # M7.1 CREATE INDEX CONCURRENTLY live bug — STATE.md / RESEARCH Pitfall 2).
    #
    # This control is PERMANENT: there is no GRANT-back in the downgrade() because
    # restoring UPDATE/DELETE privileges on an audit table is a superuser-only
    # operation that must be deliberate, not automated (D-M8-04 locked).
    with op.get_context().autocommit_block():
        op.execute("REVOKE UPDATE, DELETE ON audit_events FROM sdlc_app")

    # Step 2: Composite index for SC-08 / REQ-M8-08 — < 200ms at 10M rows.
    #
    # Index: (tenant_id, resource_id, created_at DESC)
    # resource_id stores run_id per the existing signals.py pattern — no new ORM column.
    # This covers the canonical run-scoped audit query:
    #   WHERE tenant_id = X AND resource_id = run_id ORDER BY created_at DESC
    #
    # Additive to the M7.1 ix_audit_tenant_created (tenant_id, created_at); both
    # indexes coexist. The query planner will choose based on the WHERE clause shape.
    #
    # CREATE INDEX CONCURRENTLY requires autocommit — same mechanism as 0006 step 6.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_audit_tenant_run_created "
            "ON audit_events (tenant_id, resource_id, created_at DESC)"
        )

    print(
        "\n[0008] audit_events append-only enforced: "
        "REVOKE UPDATE, DELETE FROM sdlc_app. "
        "Composite index ix_audit_tenant_run_created created (CONCURRENTLY)."
    )


def downgrade() -> None:
    # Drop the composite index (CONCURRENTLY — no table lock).
    # IF NOT EXISTS / CONCURRENTLY requires autocommit_block() — same rule as upgrade().
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_audit_tenant_run_created"
        )

    # NOTE: The REVOKE is intentionally NOT reversed here.
    # Re-granting UPDATE, DELETE on audit_events to sdlc_app is a superuser-only
    # operation that must never be automated or triggered by a downgrade script.
    # If a rollback is truly needed in production, a DBA must manually run:
    #   GRANT UPDATE, DELETE ON audit_events TO sdlc_app;
    # (D-M8-04 locked decision — append-only is permanent by design.)

    print(
        "\n[0008 downgrade] ix_audit_tenant_run_created dropped. "
        "REVOKE NOT reversed — append-only is permanent; DBA must GRANT back manually."
    )
