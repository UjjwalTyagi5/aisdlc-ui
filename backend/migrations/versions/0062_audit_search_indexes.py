"""Indexes for the audit filters that currently scan.

TWO DIFFERENT QUERIES BENEFIT, and only one of them is about search.

1. THE SCOPED READ, which runs on every request from a caller who is not org-wide.
   `list_audit_events` narrows a bu_admin or security_engineer to their own units with

       payload->>'workspace_id' IN (...)  OR  payload->>'project_id' IN (...)
       OR (payload->>'scope_kind' = 'business_unit' AND payload->>'scope_id' IN (...))

   `->>` on jsonb cannot use a plain GIN index, so every one of those reads is a
   sequential scan today. Expression indexes are what make them seekable. This is not
   speculative: it is the normal page load for two of the three roles that hold
   `audit:view`.

2. SEARCH. `_search_clause` resolves a term to ids and then matches those columns by
   equality — already indexable — but keeps an ILIKE on `event_type` and
   `resource_type` for the short vocabulary strings. An OR is only as indexable as its
   worst branch, so one un-indexed ILIKE turns the whole clause into a scan. Trigram
   indexes fix those two branches.

`actor_id` gets an ordinary btree: it serves the actor dropdown (an equality filter
that runs today) as well as the id branches of search.

pg_trgm IS OPTIONAL HERE. Creating an extension needs privileges the app role does not
have, and an environment where the migration runs unprivileged must still get the rest
of the indexes rather than failing the deploy. The DO block below degrades to "no
trigram indexes, everything else applied" and says so in a NOTICE.

Revision ID: 0062_audit_search_indexes
Revises: 0061_audit_tenant_created_index
"""
from alembic import op

revision = "0062_audit_search_indexes"
down_revision = "0061_audit_tenant_created_index"
branch_labels = None
depends_on = None

# The payload keys the scope filter reads. Kept as a list because the filter reads all
# three and an index on two of them still leaves the OR unindexable.
_PAYLOAD_KEYS = ("workspace_id", "project_id", "scope_id")


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_actor ON audit_events (actor_id)")

    for key in _PAYLOAD_KEYS:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_audit_payload_{key} "
            f"ON audit_events ((payload->>'{key}'))"
        )

    op.execute(
        """
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS pg_trgm;
            CREATE INDEX IF NOT EXISTS ix_audit_event_type_trgm
                ON audit_events USING gin (event_type gin_trgm_ops);
            CREATE INDEX IF NOT EXISTS ix_audit_resource_type_trgm
                ON audit_events USING gin (resource_type gin_trgm_ops);
        EXCEPTION WHEN insufficient_privilege OR undefined_file THEN
            RAISE NOTICE 'pg_trgm unavailable — audit search will scan on the ILIKE '
                         'branches. Install the extension as a superuser and re-run.';
        END
        $$;
        """
    )


def downgrade() -> None:
    for name in (
        "ix_audit_resource_type_trgm", "ix_audit_event_type_trgm",
        "ix_audit_payload_scope_id", "ix_audit_payload_project_id",
        "ix_audit_payload_workspace_id", "ix_audit_actor",
    ):
        op.execute(f"DROP INDEX IF EXISTS {name}")
