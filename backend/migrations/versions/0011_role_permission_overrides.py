"""Let an organization retune a built-in role, durably.

THE PROBLEM THIS SOLVES. `role_permissions` already holds every built-in role's
permission set, and it looks editable — it is an ordinary table. It is not. It is
RECONCILED FROM CODE on every boot by `seed_rbac_catalog`, which deletes any edge the
`_ROLE_PERMISSIONS` matrix does not declare. An edit made from the Roles page would
therefore work perfectly until the next restart and then silently revert, which is
worse than not offering the edit at all.

TWO TABLES, TWO OWNERS. `role_permissions` stays exactly as it is: the SHIPPED DEFAULT,
owned by the code, reconciled on boot, the thing a "reset to defaults" button restores
to. `role_permission_overrides` is owned by the organization's admin and is never
touched by the seeder. Effective = the override when one exists, the default otherwise.

WHOLE SET, NOT A DELTA. A role with any override rows holds EXACTLY those permissions;
there is no per-permission granted/revoked flag. A delta representation has to answer
"what happens when the shipped default later gains a permission this org had removed",
and every answer to that is a surprise. Taking ownership of a role is explicit: you
hold what you chose, the UI keeps showing you what shipped, and reset gives it back.

TENANT-SCOPED, unlike `role_permissions` which is global. One organization retuning its
Developer role must not change another's — and the global table could not express that,
which is the second reason this is not just a nullable column on it.

Revision ID: 0011_role_permission_overrides
Revises: 0010_governance_requests
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_role_permission_overrides"
down_revision = "0010_governance_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "role_permission_overrides",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_name", sa.String(length=64), nullable=False),
        sa.Column("permission_name", sa.String(length=64), nullable=False),
        # Who last took ownership of this role, and when. A permission set that
        # differs from the shipped default is exactly the thing an auditor asks
        # about, and "who widened the Developer role" needs an answer on the row.
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("tenant_id", "role_name", "permission_name"),
        # FKs onto the same catalogue tables `role_permissions` references, so an
        # override cannot name a role or a permission that does not exist. This is
        # what makes "add a new permission" a matter of inserting into
        # `permissions` — until it is there, nothing can grant it by typo.
        sa.ForeignKeyConstraint(["role_name"], ["roles.name"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["permission_name"], ["permissions.name"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_role_permission_overrides_lookup",
        "role_permission_overrides",
        ["tenant_id", "role_name"],
    )

    op.execute("ALTER TABLE role_permission_overrides ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON role_permission_overrides "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON role_permission_overrides "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute("ALTER TABLE role_permission_overrides FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                -- DELETE is granted here, unlike on the audit-shaped tables: "reset
                -- this role to its defaults" IS deleting the override rows, and it
                -- has to be reachable. The trail of who changed what lives in
                -- audit_events, which is append-only.
                GRANT SELECT, INSERT, UPDATE, DELETE ON role_permission_overrides
                    TO sdlc_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON role_permission_overrides")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON role_permission_overrides")
    op.drop_index(
        "ix_role_permission_overrides_lookup", table_name="role_permission_overrides"
    )
    op.drop_table("role_permission_overrides")
