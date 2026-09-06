"""Permission for one consumer to read one version that is not the published one.

THE THREE CASES THIS EXISTS FOR, and only these. Everything else — the normal path —
needs no request at all, because the owning role already signed the version once and
that signature serves every consumer. Requiring each consumer to ask each time is up to
seventy-two approval pairs per project, rubber-stamped inside a week, producing an audit
trail that looks like scrutiny and records none.

  an unpublished draft   the owner has not signed it; the consumer is asking them to
                         vouch for unfinished work
  a superseded version   pinning an older one after a newer is published, usually a
                         mistake and occasionally deliberate
  cross-project reuse    another team's work, where ownership and blast radius genuinely
                         differ

A GRANT IS NARROW ON PURPOSE: one version, one consuming stage. Not "design may read
drafts", which would be a standing licence indistinguishable from turning enforcement
off. When the next draft appears, it is a new question.

WHY A TABLE RATHER THAN READING THE APPROVED REQUEST. A governance request records that
a decision was TAKEN; this records that a permission IS IN FORCE. Revoking one should
not mean rewriting history, and the read path should not have to understand the
governance schema to answer "may this agent read this".

RLS KEYS OFF `app.current_tenant_id`, NOT `app.tenant_id`.

Revision ID: 0047_consumption_grants

Revises: 0046_artifact_consumptions
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0047_consumption_grants"
down_revision = "0046_artifact_consumptions"
branch_labels = None
depends_on = None

_TABLE = "artifact_consumption_grants"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("consumer_stage", sa.String(32), nullable=False),
        # The owning-role human who allowed it, and why. NOT NULL: a grant nobody
        # made is not a grant, and the reason is what a later reader needs to judge
        # whether it still applies.
        sa.Column("granted_by", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        # The governance request this came from, so the decision is traceable back to
        # its queue entry. Nullable: a future admin-issued grant would have no request.
        sa.Column("request_id", postgresql.UUID(as_uuid=True)),
        sa.Column("granted_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        # Revocation, rather than deletion — the run that read under this grant must
        # keep being explicable.
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by", sa.String(255)),

        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["artifact_versions.id"],
                                ondelete="CASCADE"),
        # One live grant per (version, consumer). A second would not mean more access,
        # only two rows to revoke and one of them missed.
        sa.UniqueConstraint("version_id", "consumer_stage",
                            name="uq_consumption_grant_version_consumer"),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_by IS NOT NULL",
            name="ck_consumption_grant_revoked_by_someone",
        ),
    )

    op.create_index("ix_consumption_grants_tenant_id", _TABLE, ["tenant_id"])
    # THE READ PATH: "is there a live grant for this consumer on this stage".
    op.create_index(
        "ix_consumption_grants_live", _TABLE,
        ["project_id", "consumer_stage"],
        postgresql_where=sa.text("revoked_at IS NULL"),
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


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_insert ON {_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
    op.drop_index("ix_consumption_grants_live", table_name=_TABLE)
    op.drop_index("ix_consumption_grants_tenant_id", table_name=_TABLE)
    op.drop_table(_TABLE)
