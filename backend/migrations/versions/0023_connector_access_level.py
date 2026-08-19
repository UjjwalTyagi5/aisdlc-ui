"""Connector grants carry an access level, and projects may narrow it.

`integration_grants` recorded THAT a Business Unit may use Jira and never HOW, so
every grant meant the widest thing it could: an agent granted a connector to read a
backlog could also transition items and post comments. Connectors have separated
`read_adapter` from `write_adapter` since milestone 3 — the split existed at the
point of USE and had no counterpart at the point of PERMISSION.

TWO COLUMNS, TWO LEVELS OF THE CASCADE:

  integration_grants.access          what the ORGANISATION allows this unit
  project_connector_access.access    what the unit's admin allows one project

A project row is optional and means "narrowed". Its ABSENCE means the project
inherits the unit's level rather than meaning "no access" — otherwise adding this
table would revoke every project's integrations on deploy. The intersection is
computed in `shared/authz/connector_access.narrow()`, which is also the only place
that knows read ∩ write is EMPTY rather than "one of them".

EXISTING ROWS BECOME read_write, NOT read. Tightening them silently would stop
agents mid-flight that have been writing work items legitimately for months — a
migration is the wrong place to revoke a permission nobody has reviewed. The
server_default is therefore read_write for the backfill and is DROPPED immediately
after, so a new grant must name its level and the application defaults it to `read`
(`connector_access.DEFAULT_ACCESS`). The result: nothing breaks today, and nothing
acquires write tomorrow without somebody choosing it.

Revision ID: 0023_connector_access_level
Revises: 0022_notification_scope
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0023_connector_access_level"
down_revision = "0022_notification_scope"
branch_labels = None
depends_on = None

# Mirrors ACCESS_LEVELS in shared/authz/connector_access.py. Duplicated here on
# purpose: a migration must not import application code that may be refactored out
# from under it years later, and the CHECK is the database's own statement of the
# vocabulary.
_LEVELS = ("read", "write", "read_write")
_CHECK = "access IN (" + ", ".join(f"'{lvl}'" for lvl in _LEVELS) + ")"


def upgrade() -> None:
    # ── the unit's level ──────────────────────────────────────────────────────
    op.add_column(
        "integration_grants",
        sa.Column(
            "access",
            sa.String(length=16),
            nullable=False,
            # Backfills every existing row. Dropped below.
            server_default="read_write",
        ),
    )
    op.create_check_constraint("ck_integration_grant_access", "integration_grants", _CHECK)
    # New rows must name a level. The application supplies `read` when the caller
    # does not, which is where least-privilege-by-default actually lives — a
    # server_default of 'read' here would silently widen nothing but would also let
    # a caller omit the field and not notice which level they got.
    op.alter_column("integration_grants", "access", server_default=None)

    # ── the project's narrowing ───────────────────────────────────────────────
    op.create_table(
        "project_connector_access",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        # 'connector' | 'mcp', matching integration_grants.kind.
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("target_ref", sa.String(length=255), nullable=False),
        sa.Column("access", sa.String(length=16), nullable=False),
        sa.Column("granted_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("tenant_id", "project_id", "kind", "target_ref"),
        sa.CheckConstraint(_CHECK, name="ck_project_connector_access_level"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_project_connector_access_lookup",
        "project_connector_access",
        ["tenant_id", "project_id", "kind", "target_ref"],
    )

    op.execute("ALTER TABLE project_connector_access ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON project_connector_access "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON project_connector_access "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute("ALTER TABLE project_connector_access FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON project_connector_access TO sdlc_app;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_table("project_connector_access")
    op.drop_constraint("ck_integration_grant_access", "integration_grants", type_="check")
    op.drop_column("integration_grants", "access")
