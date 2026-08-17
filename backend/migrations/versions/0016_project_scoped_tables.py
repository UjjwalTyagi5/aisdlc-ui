"""The last three tables the API was answering empty for.

  agent_access_overrides   per-project adjustments to what a role may do with an agent
  project_integration_credentials   a project's own identity against a granted tool
  cross_bu_grants          a contributor lent from one unit to another unit's project

Three unrelated features, one migration, because each is a single table with no
dependency on the others and splitting them would be three migrations that always run
together.

WHY EACH IS ITS OWN TABLE RATHER THAN A COLUMN SOMEWHERE:

`agent_access_overrides` is keyed on (project, role, phase) and there is no row it
could hang off — a role is global, a project is not, and the override belongs to
neither alone. Involvement is stored as the FULL level rather than a delta, so
"what does a QA reach on this project" is one lookup instead of a base plus a patch.

`project_integration_credentials` is keyed on the OWNER as well as the project. A
credential authenticates a PERSON against a tool, not a project: a repo bot, a board
account, a database role are each somebody's. Keyed on the project alone, the second
contributor to configure Jira silently replaced the first — same key, same row — and
neither could tell.

NO SECRET IS STORED HERE. `secret_ref` points into the secret store, exactly as
`model_providers.secret_ref` does, and `has_secret` records that a value was entered —
which is the only part of a secret a UI should ever show. A column that could hold the
value would eventually hold it.

`cross_bu_grants` records a seat, not a membership. The whole point of a loan is that
the person's home unit does not change: a row in `role_bindings` would make them a
member of the borrowing unit and lose the fact that somebody else still owns them.

Revision ID: 0016_project_scoped_tables
Revises: 0015_integration_grants
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016_project_scoped_tables"
down_revision = "0015_integration_grants"
branch_labels = None
depends_on = None

_TABLES = (
    "agent_access_overrides",
    "project_integration_credentials",
    "cross_bu_grants",
)


def upgrade() -> None:
    # ── agent access overrides ───────────────────────────────────────────────
    op.create_table(
        "agent_access_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        # A built-in role name or a custom role's uuid, as text for both.
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        # The FULL level, not a delta — see the module docstring.
        sa.Column("involvement", sa.String(length=16), nullable=False),
        sa.Column("set_by", sa.String(length=255), nullable=True),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        # One answer per (project, role, phase). Two rows would make "what does a QA
        # reach here" ambiguous, and whichever the query happened to read would win.
        sa.UniqueConstraint("project_id", "role", "phase", name="uq_agent_access_override"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "involvement IN ('none', 'use', 'primary', 'owner')",
            name="ck_agent_access_involvement",
        ),
    )
    op.create_index(
        "ix_agent_access_overrides_project", "agent_access_overrides", ["tenant_id", "project_id"]
    )

    # ── project integration credentials ──────────────────────────────────────
    op.create_table(
        "project_integration_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        # WHOSE credential this is. Part of the key — see the module docstring.
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.String(length=255), nullable=False),
        sa.Column("account", sa.String(length=255), nullable=True),
        # A POINTER into the secret store, never the value.
        sa.Column("secret_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "owner_id", "kind", "target_id", name="uq_project_credential"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.CheckConstraint("kind IN ('connector', 'mcp')", name="ck_project_credential_kind"),
    )
    op.create_index(
        "ix_project_credentials_project",
        "project_integration_credentials",
        ["tenant_id", "project_id"],
    )

    # ── cross-BU grants ──────────────────────────────────────────────────────
    op.create_table(
        "cross_bu_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        # The unit that OWNS them, and the project they are lent into. The borrowing
        # unit is derivable from the project, and storing it as well would be a second
        # copy of the same fact.
        sa.Column("parent_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        # One loan per person per project. Lending the same person twice is the same
        # seat, not two.
        sa.UniqueConstraint("user_id", "project_id", name="uq_cross_bu_grant"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_cross_bu_grants_project", "cross_bu_grants", ["tenant_id", "project_id"])
    op.create_index("ix_cross_bu_grants_parent", "cross_bu_grants", ["tenant_id", "parent_workspace_id"])

    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
        )
        op.execute(
            f"CREATE POLICY tenant_isolation_insert ON {table} "
            "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
        )
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        DO $$
        DECLARE t text;
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                FOREACH t IN ARRAY ARRAY['agent_access_overrides',
                                         'project_integration_credentials',
                                         'cross_bu_grants']
                LOOP
                    EXECUTE format(
                        'GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO sdlc_app', t
                    );
                END LOOP;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_insert ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_table("cross_bu_grants")
    op.drop_table("project_integration_credentials")
    op.drop_table("agent_access_overrides")
