"""Custom roles get an owner scope and a creator.

A custom role was tenant-wide and anonymous: any `role:manage` holder could create
one and it was assignable anywhere in the organization. Two consequences:

  * A Business Unit Admin had no way to define a role for their own unit without
    defining it for every unit.
  * Nothing recorded who created a role, which is the first question asked when one
    turns out to carry more than it should.

`scope_kind` / `scope_id` say who OWNS the role and bound where it may be assigned;
existing rows are backfilled to organization scope, which is what they effectively
were.

Revision ID: 0004_custom_role_scope
Revises: 0003_scope_chain_and_expiry
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_custom_role_scope"
down_revision = "0003_scope_chain_and_expiry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_roles",
        sa.Column("scope_kind", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "custom_roles",
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "custom_roles",
        sa.Column("created_by", sa.String(length=255), nullable=True),
    )

    # Backfill BEFORE the NOT NULL / CHECK: every existing role was tenant-wide, which
    # is organization scope with the tenant as the scope id.
    op.execute(
        "UPDATE custom_roles SET scope_kind = 'organization', scope_id = tenant_id "
        "WHERE scope_kind IS NULL"
    )
    op.alter_column("custom_roles", "scope_kind", nullable=False)
    op.alter_column("custom_roles", "scope_id", nullable=False)

    op.create_check_constraint(
        "ck_custom_role_scope_kind",
        "custom_roles",
        "scope_kind IN ('organization', 'business_unit')",
    )
    op.create_index("ix_custom_roles_scope_id", "custom_roles", ["scope_id"])

    # The uniqueness rule changes with ownership: two business units may each define
    # a role called "Reviewer" without colliding, which a tenant-wide unique name
    # forbade. Uniqueness is now per owner scope.
    op.drop_constraint("uq_custom_role_tenant_name", "custom_roles", type_="unique")
    op.create_unique_constraint(
        "uq_custom_role_scope_name", "custom_roles", ["tenant_id", "scope_id", "name"]
    )


def downgrade() -> None:
    # The upgrade widened uniqueness from (tenant_id, name) to
    # (tenant_id, scope_id, name), so two units may each own a role called "Reviewer".
    # Narrowing back cannot succeed while both exist — keep one per (tenant_id, name)
    # and drop the rest, or CREATE UNIQUE fails and the whole downgrade with it.
    #
    # Latent rather than currently failing: this database has no such pair today, which
    # is exactly why it would have surprised whoever first ran a downgrade after two
    # units happened to pick the same name.
    #
    # `ctid` is Postgres's physical row identifier — used here because custom_roles has
    # no ordering column that would make "which duplicate survives" meaningful. Any
    # choice is arbitrary; this one is at least deterministic within a single run.
    op.execute(
        "DELETE FROM custom_roles a USING custom_roles b "
        " WHERE a.tenant_id = b.tenant_id AND a.name = b.name AND a.ctid > b.ctid"
    )
    op.drop_constraint("uq_custom_role_scope_name", "custom_roles", type_="unique")
    op.create_unique_constraint(
        "uq_custom_role_tenant_name", "custom_roles", ["tenant_id", "name"]
    )
    op.drop_index("ix_custom_roles_scope_id", table_name="custom_roles")
    op.drop_constraint("ck_custom_role_scope_kind", "custom_roles", type_="check")
    op.drop_column("custom_roles", "created_by")
    op.drop_column("custom_roles", "scope_id")
    op.drop_column("custom_roles", "scope_kind")
