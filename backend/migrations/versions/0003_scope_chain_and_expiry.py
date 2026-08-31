"""Fourth scope level (workstream) + time-bound role assignments.

Two things `can_perform` needs that the schema could not express:

1. **workstream** — the scope chain is organization -> business_unit -> project ->
   workstream. Only the first three existed, and `role_bindings.scope_kind` was
   CHECK-constrained to them, so a workstream-scoped assignment was unrepresentable.

2. **expires_at** — a temporary elevation ("developer gets project_admin for the
   release window") had no way to end. `status` is a lifecycle flag someone must
   remember to flip; an expiry is enforced by the clock. Without it the
   "elevated role within expiry / expired elevation" distinction cannot be tested,
   because it cannot be stored.

`granted_by` is added alongside: a time-bound grant is exactly the kind you get asked
about later, and "who gave this person project_admin" should not require reading an
audit log that does not exist yet.

Revision ID: 0003_scope_chain_and_expiry
Revises: 0002_org_model_grants
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_scope_chain_and_expiry"
down_revision = "0002_org_model_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── workstreams ──────────────────────────────────────────────────────────
    op.create_table(
        "workstreams",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # Real FK: a workstream cannot outlive its project, and the scope chain walks
        # up through this column. Unlike role_bindings.scope_id (which points at one
        # of three tables and therefore cannot have an FK), this one is unambiguous.
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        # RLS anchor — policy column, no FK, mirroring every other tenant table.
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="active"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "slug", name="uq_workstream_project_slug"),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name="ck_workstream_status"
        ),
    )
    op.create_index(op.f("ix_workstreams_project_id"), "workstreams", ["project_id"])
    op.create_index(op.f("ix_workstreams_tenant_id"), "workstreams", ["tenant_id"])

    op.execute("ALTER TABLE workstreams ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON workstreams "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON workstreams "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute("ALTER TABLE workstreams FORCE ROW LEVEL SECURITY")

    # ── time-bound assignments ───────────────────────────────────────────────
    op.add_column(
        "role_bindings",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "role_bindings",
        sa.Column("granted_by", sa.String(length=255), nullable=True),
    )
    # Partial index: only time-bound rows are ever swept for expiry, and they are the
    # small minority. Indexing the whole column would be mostly NULLs.
    op.create_index(
        "ix_role_bindings_expires_at",
        "role_bindings",
        ["expires_at"],
        postgresql_where=sa.text("expires_at IS NOT NULL"),
    )

    # ── extend the scope chain ───────────────────────────────────────────────
    op.drop_constraint("ck_role_binding_scope_kind", "role_bindings", type_="check")
    op.create_check_constraint(
        "ck_role_binding_scope_kind",
        "role_bindings",
        "scope_kind IN ('organization', 'business_unit', 'project', 'workstream')",
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON workstreams TO sdlc_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # Workstream-scoped bindings go before the constraint that forbids them. This
    # downgrade also drops the `workstreams` table below, so a binding pointing at one
    # is about to reference nothing whatever happens here — narrowing the CHECK while
    # such rows exist just makes Postgres reject the ALTER first.
    #
    # Not yet reachable in practice (nothing grants at workstream scope today), which
    # is exactly why it would have surprised whoever hit it first.
    op.execute("DELETE FROM role_bindings WHERE scope_kind = 'workstream'")
    op.drop_constraint("ck_role_binding_scope_kind", "role_bindings", type_="check")
    op.create_check_constraint(
        "ck_role_binding_scope_kind",
        "role_bindings",
        "scope_kind IN ('organization', 'business_unit', 'project')",
    )
    op.drop_index("ix_role_bindings_expires_at", table_name="role_bindings")
    op.drop_column("role_bindings", "granted_by")
    op.drop_column("role_bindings", "expires_at")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON workstreams")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON workstreams")
    op.drop_index(op.f("ix_workstreams_tenant_id"), table_name="workstreams")
    op.drop_index(op.f("ix_workstreams_project_id"), table_name="workstreams")
    op.drop_table("workstreams")
