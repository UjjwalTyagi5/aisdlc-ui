"""SCIM Wave A — users.active column + externalId uniqueness constraint.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-09

Adds two schema changes required for SCIM 2.0 provisioning (REQ-M7-16, D-01, D-05):

1. ``users.active``  Boolean NOT NULL DEFAULT true
   - Soft-deactivate flag set by SCIM deprovision (DELETE /Users/{id}).
   - False means the user is deprovisioned; future logins are blocked.
   - D-01: reversible — re-provision flips active back to true on the same row,
     preserving the audit trail (no hard-delete).

2. ``UNIQUE(external_id, tenant_id)``  — constraint name uq_users_external_id_tenant
   - DB-enforced idempotency key for SCIM provision (REQ-M7-16).
   - Prevents duplicate rows on concurrent IdP POST /Users calls for the same user.
   - Using the DB constraint instead of an app-level check-then-insert avoids the
     TOCTOU race under concurrent provisioning requests.

The ``users`` table was created in migration 0007. This migration only adds a column
and a constraint to that existing table; it does NOT create a new table.

Must run as superuser (POSTGRES_MIGRATIONS_CONN_STRING) — same requirement as 0006/0007
because ``users`` has FORCE ROW LEVEL SECURITY.  The ``active`` column has a server-side
default so existing rows are backfilled without a data migration step.
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_unique_constraint(
        "uq_users_external_id_tenant",
        "users",
        ["external_id", "tenant_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_users_external_id_tenant", "users", type_="unique")
    op.drop_column("users", "active")
