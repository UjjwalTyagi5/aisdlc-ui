"""Model gateway cascade — reconcile org_model_grants, add project_model_selections.

WHY THIS EXISTS RATHER THAN main's 0035/0036. Those two migrations arrived with the
merge from main and declared `down_revision = "0034_merge_skills_budgets"` — a revision
that does not exist on this branch, because 0001_baseline squashed that entire lineage
into 0001-0016. Alembic could not even BUILD its revision map: every command, including
`current` and `heads`, died with KeyError('0034_merge_skills_budgets'). Nothing could
migrate at all, so the model gateway ran against a schema from 0002 while its code
expected the shape from 0035.

That mismatch was not subtle in effect but was silent in the UI: `model_grants.py`
joins `model_providers.id` (uuid) to `org_model_grants.credential_id`, which 0002
created as varchar(36). Every read raised `operator does not exist: uuid = character
varying`, so the model picker showed "Connect a model provider" forever and no write
ever landed.

This migration carries the same END STATE on this branch's lineage. 0002 already
created `org_model_grants` (with RLS, its index and the app grant), so the table is
ALTERED here rather than created — recreating it would make the two migrations fight
over the same name on any database that has already run 0002.

THE THREE TYPE CHANGES ARE THE POINT:
  credential_id      varchar(36) -> uuid + FK to model_providers, so the join in
                     model_grants.py is expressible at all. ON DELETE CASCADE: a grant
                     naming a deleted connection is not a permission, it is a dangling
                     row that reads like one.
  business_unit_ids  uuid[] -> JSONB, matching what the service reads and writes.
  created_by         added. 0035 has it NOT NULL; it is added with a server_default
                     here so the migration also works on a database that already holds
                     grants, and the default is dropped immediately afterwards so new
                     rows must name their author.

Revision ID: 0017_model_gateway_cascade
Revises: 0016_project_scoped_tables
"""
import uuid as _uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0017_model_gateway_cascade"
down_revision = "0016_project_scoped_tables"
branch_labels = None
depends_on = None


def _force_rls(table: str) -> None:
    """ENABLE + both policies + FORCE. All four, or the table is not isolated."""
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


def _grant_app(table: str) -> None:
    """Migrations run as superuser, so a new table starts ungranted for the app role.

    Guarded on the role existing so a database provisioned without `sdlc_app` (CI, a
    throwaway test DB) still migrates instead of failing on a missing grantee.
    """
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO sdlc_app;
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    # ── org_model_grants: reshape 0002's table to the gateway's contract ──────
    op.execute("ALTER TABLE org_model_grants ALTER COLUMN business_unit_ids DROP DEFAULT")
    op.execute(
        "ALTER TABLE org_model_grants ALTER COLUMN business_unit_ids "
        "TYPE jsonb USING to_jsonb(business_unit_ids)"
    )
    op.execute(
        "ALTER TABLE org_model_grants ALTER COLUMN business_unit_ids "
        "SET DEFAULT '[]'::jsonb"
    )

    op.execute(
        "ALTER TABLE org_model_grants ALTER COLUMN credential_id "
        "TYPE uuid USING NULLIF(credential_id, '')::uuid"
    )
    op.create_foreign_key(
        "fk_org_model_grants_credential", "org_model_grants", "model_providers",
        ["credential_id"], ["id"], ondelete="CASCADE",
    )

    op.add_column(
        "org_model_grants",
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
    )
    op.alter_column("org_model_grants", "created_by", server_default=None)

    # Uniqueness in two halves: a plain constraint cannot cover the NULL case, because
    # NULL <> NULL would let duplicate "any key" grants for one model slip straight past
    # it — which is the shape most grants actually have.
    op.create_unique_constraint(
        "uq_org_grant_cred", "org_model_grants",
        ["tenant_id", "provider", "model_id", "credential_id"],
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_org_grant_null_cred ON org_model_grants "
        "(tenant_id, provider, model_id) WHERE credential_id IS NULL"
    )

    # ── project_model_selections ─────────────────────────────────────────────
    # No row (or an empty `selected`) means "inherit the BU's full allowed set" — that
    # rule lives in the service layer, not here, because "no row" cannot be expressed
    # as a constraint.
    op.create_table(
        "project_model_selections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "project_id", UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True,
        ),
        sa.Column("selected", JSONB, nullable=False, server_default="[]"),
        sa.Column("default_key", sa.String(255), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
    )
    op.create_index(
        "ix_project_model_selections_tenant_id", "project_model_selections", ["tenant_id"]
    )
    _force_rls("project_model_selections")
    _grant_app("project_model_selections")

    # ── model_providers: approval workflow + keyless onboarding ──────────────
    op.add_column("model_providers", sa.Column("approval_status", sa.String(20), nullable=False, server_default="active"))
    op.add_column("model_providers", sa.Column("approval_decided_by", sa.String(255), nullable=True))
    op.add_column("model_providers", sa.Column("approval_decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("model_providers", sa.Column("approval_reason", sa.Text(), nullable=True))

    # Keyless onboarding (was main's 0036): a connection's models can be granted
    # centrally while a BU/project supplies its own key later, so there is nothing to
    # put in the vault at create time.
    op.alter_column("model_providers", "secret_ref", nullable=True)


def downgrade() -> None:
    op.alter_column("model_providers", "secret_ref", nullable=False)
    op.drop_column("model_providers", "approval_reason")
    op.drop_column("model_providers", "approval_decided_at")
    op.drop_column("model_providers", "approval_decided_by")
    op.drop_column("model_providers", "approval_status")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON project_model_selections")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON project_model_selections")
    op.drop_table("project_model_selections")

    op.execute("DROP INDEX IF EXISTS uq_org_grant_null_cred")
    op.drop_constraint("uq_org_grant_cred", "org_model_grants", type_="unique")
    op.drop_column("org_model_grants", "created_by")
    op.drop_constraint("fk_org_model_grants_credential", "org_model_grants", type_="foreignkey")
    op.execute(
        "ALTER TABLE org_model_grants ALTER COLUMN credential_id "
        "TYPE varchar(36) USING credential_id::text"
    )
    op.execute("ALTER TABLE org_model_grants ALTER COLUMN business_unit_ids DROP DEFAULT")
    op.execute(
        "ALTER TABLE org_model_grants ALTER COLUMN business_unit_ids "
        "TYPE uuid[] USING ARRAY(SELECT jsonb_array_elements_text(business_unit_ids)::uuid)"
    )
    op.execute("ALTER TABLE org_model_grants ALTER COLUMN business_unit_ids SET DEFAULT '{}'::uuid[]")
