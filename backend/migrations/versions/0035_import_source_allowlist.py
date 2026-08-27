"""Add import_source_allowlist table (Agent Studio sub-project 5: import + supply-chain screening).

Org-Admin-curated list of approved external source URL prefixes, used by the
third of three import screens (prompt-injection, credential leakage,
provenance). Append-only — no UPDATE/DELETE route is planned; an Org Admin
who wants to remove an entry gets that as explicit follow-up work, not this
migration's job. Nothing reads or writes this table yet (that lands in later
tasks of this sub-project); this migration is storage-layer only.

Revision ID: 0035_import_source_allowlist
Revises: 0034_agent_default_evaluations
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0035_import_source_allowlist"
down_revision = "0034_agent_default_evaluations"
branch_labels = None
depends_on = None

_TABLE = "import_source_allowlist"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_pattern", sa.String(length=500), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_import_source_allowlist_tenant_id", _TABLE, ["tenant_id"]
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
                -- Append-only, mirroring agent_default_evaluations: INSERT and
                -- SELECT only, so a row cannot be rewritten or removed even by
                -- a bug in the service layer.
                GRANT SELECT, INSERT ON import_source_allowlist TO sdlc_app;
                REVOKE UPDATE, DELETE ON import_source_allowlist FROM sdlc_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_insert ON {_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
    op.drop_table(_TABLE)
