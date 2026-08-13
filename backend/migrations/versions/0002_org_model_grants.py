"""Org → BU → project model grant cascade.

The Organization Admin's catalogue policy: which (provider, model, subscription)
triples the organization permits, and how far each approval reaches. This is the
ONLY place a model enters the platform's catalogue.

A `global` grant lands in every business unit automatically; a `specific` one lands
only in the units named on it. A Business Unit Admin cannot widen either — what a
unit may use is DERIVED from these rows, never a list the unit curates. That is why
there is no per-unit grant table: a second table would let a unit's own admin edit
their entitlement, which is the opposite of "the Org Admin governs the catalogue".

business_unit_ids is an array column rather than a join table on purpose. A grant is
edited as a whole ("these units, and no others"), never one membership at a time, so
a join table would add a second write path with no reader that needs it.

Revision ID: 0002_org_model_grants
Revises: 0001_baseline
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_org_model_grants"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_model_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # RLS anchor: no FK, matching every other tenant-scoped table — tenant_id is
        # a policy column, not a relational one.
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        # Which subscription serves it. NULL = "whichever key the provider has",
        # which is what every grant meant before a provider could hold two. Kept as
        # a plain string (not an FK) so deleting a connection leaves the grant legible
        # rather than silently cascading a permission away.
        sa.Column("credential_id", sa.String(length=36), nullable=True),
        sa.Column(
            "visibility", sa.String(length=16), nullable=False, server_default="global"
        ),
        # Meaningful only when visibility = 'specific'.
        sa.Column(
            "business_unit_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "visibility IN ('global', 'specific')", name="ck_org_model_grants_visibility"
        ),
    )
    op.create_index(
        op.f("ix_org_model_grants_tenant_id"), "org_model_grants", ["tenant_id"]
    )

    # No unique constraint on (tenant_id, provider, model_id, credential_id):
    # credential_id is nullable and NULLs never compare equal, so the constraint
    # would silently permit exactly the duplicates it appears to forbid. The write
    # path replaces the whole grant set in one transaction instead, which makes
    # duplicates unrepresentable rather than merely rejected.

    op.execute("ALTER TABLE org_model_grants ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON org_model_grants "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON org_model_grants "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute("ALTER TABLE org_model_grants FORCE ROW LEVEL SECURITY")

    # Migrations run as a superuser, so a new table starts with no privileges for the
    # app role and the first request would 500 with "permission denied for table
    # org_model_grants". ALTER DEFAULT PRIVILEGES usually covers this, but only where
    # an operator has already set it up — this makes the migration self-sufficient.
    #
    # Guarded on the role existing: a database provisioned without `sdlc_app` (CI, a
    # throwaway test DB) must still migrate rather than fail on a missing grantee.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON org_model_grants TO sdlc_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON org_model_grants")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON org_model_grants")
    op.drop_index(op.f("ix_org_model_grants_tenant_id"), table_name="org_model_grants")
    op.drop_table("org_model_grants")
