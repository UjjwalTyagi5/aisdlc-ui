"""Composite index for the canonical audit query.

`ix_audit_tenant_run_created` was created by the original migration 0008 and lost in
the 0001 squash, alongside that migration's append-only REVOKE (restored in 0005).
Both losses were invisible until something exercised them: an index only shows up as
slowness, on a table that grows forever.

The query it serves is the one the audit UI and every compliance export issue:

    WHERE tenant_id = ? AND resource_id = ? ORDER BY created_at DESC LIMIT n

Column order matters and is not alphabetical: the two equality predicates come first
so the index can seek, then created_at DESC so the ORDER BY is satisfied by the scan
rather than by a sort of everything that matched. The name keeps its historical
"run" — the column has since become the generic `resource_id`, but renaming an index
gains nothing and breaks the test that names it.

Revision ID: 0006_audit_query_index
Revises: 0005_audit_append_only
"""
from alembic import op

revision = "0006_audit_query_index"
down_revision = "0005_audit_append_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_tenant_run_created "
        "ON audit_events (tenant_id, resource_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_audit_tenant_run_created")
