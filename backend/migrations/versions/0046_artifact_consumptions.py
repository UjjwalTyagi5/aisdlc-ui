"""Record which version each run consumed, and the per-project switch that enforces it.

TWO THINGS, and the flag is the one that makes this safe to ship.

`artifact_consumptions` answers "what did this run actually build on" a month later.
Today nothing records it: every consumer takes the latest non-null payload ordered by
`created_at desc`, so the question has no answer at all — not a stale one, none.

`projects.enforce_artifact_publication` DEFAULTS TO FALSE. Turning enforcement on for a
project whose stages have never published anything makes every agent correctly report
"no approved upstream" — which is the right answer and looks exactly like an outage.
That has to be a deliberate switch somebody throws, not a migration that lands.

WHY A ROW PER CONSUMPTION rather than a column on `runs`. One run consumes several
upstream stages (Deployment reads testing AND security; Documentation reads six), and a
column per pair is the shape that stops working the moment an agent is added.

NO UNIQUE CONSTRAINT on (run, version). A run legitimately re-reads its upstream — an
agent may call the tool more than once in a turn — and deduplicating in the schema would
throw away the fact that it did. `consumed_at` distinguishes them.

RLS KEYS OFF `app.current_tenant_id`, NOT `app.tenant_id`. The app role is not a
Postgres superuser, so the wrong GUC name does not error — it reads as a permanently
empty table.

Revision ID: 0046_artifact_consumptions

Revises: 0045_artifact_versions
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0046_artifact_consumptions"
down_revision = "0045_artifact_versions"
branch_labels = None
depends_on = None

_TABLE = "artifact_consumptions"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        # The version that was read. CASCADE: a consumption of a version that no longer
        # exists is not evidence of anything, and versions are never deleted in normal
        # operation anyway.
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Denormalised so the evidence view can answer without a join, and so the row
        # still reads if the version is ever removed.
        sa.Column("producing_stage", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        # Who read it.
        sa.Column("consumer_stage", sa.String(32), nullable=False),
        sa.Column("consumer_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("consumed_by", sa.String(255)),
        sa.Column("consumed_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),

        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["artifact_versions.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["consumer_run_id"], ["runs.id"], ondelete="SET NULL"),
    )

    op.create_index("ix_artifact_consumptions_tenant_id", _TABLE, ["tenant_id"])
    op.create_index("ix_artifact_consumptions_version", _TABLE, ["version_id"])
    # "What did this run build on" — the question an auditor asks.
    op.create_index("ix_artifact_consumptions_run", _TABLE, ["consumer_run_id"])
    op.create_index(
        "ix_artifact_consumptions_project", _TABLE,
        ["project_id", sa.text("consumed_at DESC")],
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

    # ── the switch ───────────────────────────────────────────────────────────
    # FALSE by default and NOT NULL: an unset value must mean "behave exactly as
    # before", never "enforce". A nullable flag would make three states out of two.
    op.add_column(
        "projects",
        sa.Column("enforce_artifact_publication", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("projects", "enforce_artifact_publication")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_insert ON {_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
    op.drop_index("ix_artifact_consumptions_project", table_name=_TABLE)
    op.drop_index("ix_artifact_consumptions_run", table_name=_TABLE)
    op.drop_index("ix_artifact_consumptions_version", table_name=_TABLE)
    op.drop_index("ix_artifact_consumptions_tenant_id", table_name=_TABLE)
    op.drop_table(_TABLE)
