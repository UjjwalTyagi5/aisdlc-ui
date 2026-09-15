"""The ordering key the audit trail is now walked by.

`0061_audit_tenant_created_index` is `(tenant_id, created_at DESC)`, which was right
while the page used OFFSET: the order was satisfied by the scan and the skipping
happened above it.

The keyset walk compares the whole ordering key at once —

    WHERE (created_at, id) < (:ts, :id)
    ORDER BY created_at DESC, id DESC

— and `id` has to be IN the index for that to be a single seek to the anchor row. With
`created_at` alone the planner seeks to the timestamp and then filters on `id` across
every row sharing it, which is exactly the rows that matter: the seeded personas were
granted eleven roles inside the same second, and a trail records bursts by nature.

Supersedes nothing. 0061 still serves the `count(*)` behind "of N matching", which
never looks at `id`.

Revision ID: 0063_audit_keyset_index
Revises: 0062_audit_search_indexes
"""
from alembic import op

revision = "0063_audit_keyset_index"
down_revision = "0062_audit_search_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_tenant_created_id "
        "ON audit_events (tenant_id, created_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_audit_tenant_created_id")
