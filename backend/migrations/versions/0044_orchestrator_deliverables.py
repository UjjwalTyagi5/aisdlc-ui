"""`orchestrator_deliverables` — what the Orchestrator's agents produce.

NOT `artifacts`. That table carries `approval_status` because a STANDALONE agent
wrote the row and a human has to accept it. The Orchestrator's agents share the
standalone agents' names and capability and are a different thing: the runner is a
Project Admin who already owns all nine, and there are no gates (spec D5). So its
output is a separate concept with a separate name, and this table deliberately has
no approval column at all.

NOTHING IS EVER OVERWRITTEN. Any agent can run at any time in this engine, and the
same agent can run repeatedly in one conversation. A re-run APPENDS a row; the panel
orders by `created_at DESC` and shows every version. An UPDATE here would silently
destroy a document the user asked for.

RLS KEYS OFF `app.current_tenant_id`, NOT `app.tenant_id`. Every other tenant-scoped
policy in this database uses that setting name and `get_db_session_for_tenant` sets
it transaction-locally. A policy written against the other name matches nothing and
the table reads as permanently empty — indistinguishable from "no deliverables yet".
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0044_orchestrator_deliverables"
down_revision = "0043_deployments"
branch_labels = None
depends_on = None

_TABLE = "orchestrator_deliverables"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Nullable to match `runs.project_id`, which is nullable since 0005 for
        # webhook-triggered runs. A NOT NULL here would refuse to record a
        # deliverable for a run the platform itself allows to exist.
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),

        sa.Column("agent_id", sa.String(50), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("content", sa.Text),
        sa.Column("url", sa.Text),
        sa.Column("language", sa.String(50)),
        sa.Column("source", sa.String(50)),

        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),

        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),

        # The nine. Kept in sync with `registry.AGENT_IDS` by
        # tests/orchestrator2/test_deliverables_schema.py.
        sa.CheckConstraint(
            "agent_id IN ('requirements','design','plan','development',"
            "'code_review','security','testing','deployment','documentation')",
            name="ck_orchestrator_deliverables_agent_id",
        ),
        sa.CheckConstraint(
            "kind IN ('markdown','mermaid','openapi','code','image','download',"
            "'code-tree','file-tree','link')",
            name="ck_orchestrator_deliverables_kind",
        ),
    )

    # The panel read, exactly: this run, newest first.
    op.create_index(
        "ix_orchestrator_deliverables_run_created", _TABLE,
        ["run_id", sa.text("created_at DESC")],
    )
    op.create_index("ix_orchestrator_deliverables_tenant_id", _TABLE, ["tenant_id"])
    op.create_index("ix_orchestrator_deliverables_project_id", _TABLE, ["project_id"])

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {_TABLE} "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        f"CREATE POLICY tenant_isolation_insert ON {_TABLE} "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    # FORCE, so the table owner is subject to the policy too.
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_insert ON {_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
    op.drop_index("ix_orchestrator_deliverables_project_id", table_name=_TABLE)
    op.drop_index("ix_orchestrator_deliverables_tenant_id", table_name=_TABLE)
    op.drop_index("ix_orchestrator_deliverables_run_created", table_name=_TABLE)
    op.drop_table(_TABLE)
