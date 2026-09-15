"""The index the org-wide Audit Trail actually needs.

`ix_audit_tenant_run_created` (migration 0006) is `(tenant_id, resource_id,
created_at DESC)`, built for the RUN-scoped trail, whose query pins BOTH equality
columns. The Audit Trail page pins only the tenant and then orders by time:

    WHERE tenant_id = ? [+ filters] ORDER BY created_at DESC OFFSET n LIMIT m

With `resource_id` sitting between them that index cannot serve the ordering, so the
planner falls back to the bare `created_at` index and filters every other tenant's
rows out as it walks — or, once filters are applied, sorts the whole matching set.
The `count(*)` behind the pager was measurably a Seq Scan on this database.

This is the composite that makes both a seek: equality first, then time in the order
it is read. It also lets the count run as an index-only scan instead of a table scan.

Nothing is dropped. 0006's index is still the right one for /runs/{id}/audit, and an
audit table only ever grows — the cost of the second index is paid on insert, which
is one row per governance decision.

Revision ID: 0061_audit_tenant_created_index
Revises: 0060_custom_role_agent_access
"""
from alembic import op

revision = "0061_audit_tenant_created_index"
down_revision = "0060_custom_role_agent_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_tenant_created "
        "ON audit_events (tenant_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_audit_tenant_created")
