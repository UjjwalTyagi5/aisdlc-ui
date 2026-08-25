"""Move an integration's base_url off the personal credential onto the project.

WHY IT MOVED. Migration 0032 put `base_url` on `project_integration_credentials`,
which is keyed on the OWNER — so every project member typed their own site URL,
and any contributor could point the project's Jira, Confluence, SonarQube or
Azure DevOps at any host they liked. The organization decided *whether* a project
may use Jira; it did not decide *which* Jira, and the second is the one that
governs where the work leaves the building.

THE SPLIT IS BY WHO THE FIELD BELONGS TO:

  base_url is CONFIGURATION. One answer per project, set by whoever administers
  it (`shared.authz.project_scope.assert_can_administer_project`). Two projects
  may still point at different instances — that requirement is why the URL is
  per-project rather than per-tenant — but a developer cannot redirect one.

  account + secret are IDENTITY. They stay on the credential, keyed on the
  owner, because a repo bot or a board account is somebody's and per-person
  attribution in the target system depends on them differing.

NOT AN ORG-WIDE SETTING, deliberately. A tenant-level URL was the state before
0031 and it is wrong for the same reason: it asserts every project in a tenant
shares one instance. This is the middle position — governed, but per project.

Existing rows are carried over rather than dropped: a project whose members
already set a URL keeps it, taking the most recently updated one where members
disagreed (they had no way to coordinate, so the freshest is the best guess and
an admin can correct it).

Revision ID: 0033_project_integration_config
Revises: 0032_project_credential_base_url
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0033_project_integration_config"
down_revision = "0032_project_credential_base_url"
branch_labels = None
depends_on = None

_TABLE = "project_integration_config"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        # 'connector' | 'mcp', matching integration_grants.kind.
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.String(length=255), nullable=False),
        # Plain configuration, never a secret — it must stay readable and
        # correctable, and every member needs to SEE which instance they are
        # authenticating against even though only an admin may change it.
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("set_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        # One answer per (project, integration). A second row would make "which
        # instance does this project use" ambiguous, and whichever the query
        # happened to read would win — the same rule agent_access_overrides
        # states for its own grain.
        sa.UniqueConstraint("project_id", "kind", "target_id", name="uq_project_integration_config"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_project_integration_config_project", _TABLE, ["tenant_id", "project_id"]
    )

    # Carry over what members already set, newest per (project, kind, target).
    op.execute(
        """
        INSERT INTO project_integration_config
            (id, tenant_id, project_id, kind, target_id, base_url, set_by)
        SELECT DISTINCT ON (c.project_id, c.kind, c.target_id)
               gen_random_uuid(), c.tenant_id, c.project_id, c.kind, c.target_id,
               c.base_url, c.owner_id
        FROM project_integration_credentials c
        WHERE c.base_url IS NOT NULL AND c.base_url <> ''
        ORDER BY c.project_id, c.kind, c.target_id, c.updated_at DESC
        ON CONFLICT (project_id, kind, target_id) DO NOTHING
        """
    )

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {_TABLE} "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        f"CREATE POLICY tenant_isolation_insert ON {_TABLE} "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE
                    ON project_integration_config TO sdlc_app;
            END IF;
        END
        $$;
        """
    )

    # Only now that the values are copied does the old column go.
    op.drop_column("project_integration_credentials", "base_url")


def downgrade() -> None:
    op.add_column(
        "project_integration_credentials",
        sa.Column("base_url", sa.String(length=500), nullable=True),
    )
    # Give every owner's credential the project's URL back — the pre-0032 shape
    # had it per credential, and one shared value is the honest reconstruction.
    op.execute(
        """
        UPDATE project_integration_credentials c
        SET base_url = f.base_url
        FROM project_integration_config f
        WHERE f.project_id = c.project_id
          AND f.kind = c.kind
          AND f.target_id = c.target_id
        """
    )
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_insert ON {_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
    op.drop_table(_TABLE)
