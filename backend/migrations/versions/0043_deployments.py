"""The `deployments` table — one requested deployment action and the decision about it.

Deployment agent phase 1. Generating deployment files needs no gate; creating a
pipeline, starting a run, or applying to a cluster each change something outside the
platform, and each becomes a row here that starts `pending`.

RLS KEYS OFF `app.current_tenant_id`, NOT `app.tenant_id`. Every other tenant-scoped
policy in this database uses that setting name and `get_db_session` sets it
transaction-locally. A policy written against the other name matches nothing and the
table reads as permanently empty — which looks exactly like "no deployments yet".

`artifact:approve_deployment` is NOT created here. It already exists and is already
granted to devops_engineer, so re-granting it would be the drift `assert_rbac_catalog`
refuses to boot on.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0043_deployments"
down_revision = "0042_artifact_approve_plan"
branch_labels = None
depends_on = None

_TABLE = "deployments"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),

        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("target_kind", sa.String(50), nullable=False),
        sa.Column("environment", sa.String(100), nullable=False),
        # What the approver read. Immutable once approved.
        sa.Column("request", postgresql.JSONB, nullable=False),

        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),

        # Defaults to the UNAPPROVED value: a writer that has not been taught about
        # this column must fail closed, never deploy silently.
        sa.Column("approval_status", sa.String(20), nullable=False,
                  server_default="pending"),
        sa.Column("approved_by", sa.String(255)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("rejection_reason", sa.Text),

        sa.Column("execution_status", sa.String(30), nullable=False,
                  server_default="not_started"),
        # Non-null means this approval is spent. One approval, one deployment.
        sa.Column("executed_at", sa.DateTime(timezone=True)),
        sa.Column("external_id", sa.String(100)),
        sa.Column("external_url", sa.Text),
        sa.Column("outcome", postgresql.JSONB),

        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),

        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),

        # The state machine, in the schema rather than only in the service. A bad
        # value here is a bug that reaches an environment.
        sa.CheckConstraint(
            "approval_status IN ('pending','approved','rejected')",
            name="ck_deployments_approval_status",
        ),
        sa.CheckConstraint(
            "execution_status IN ('not_started','running','succeeded','failed',"
            "'canceled','error')",
            name="ck_deployments_execution_status",
        ),
        sa.CheckConstraint(
            "action IN ('create_pipeline','run_pipeline','direct_apply')",
            name="ck_deployments_action",
        ),
        # NOTHING EXECUTES WITHOUT AN APPROVAL. The database refuses a row that has
        # fired while unapproved, so a service-layer mistake cannot deploy.
        sa.CheckConstraint(
            "executed_at IS NULL OR approval_status = 'approved'",
            name="ck_deployments_executed_only_when_approved",
        ),
        # An approval names its approver. A row approved by nobody is not approved.
        sa.CheckConstraint(
            "approval_status <> 'approved' OR approved_by IS NOT NULL",
            name="ck_deployments_approved_by_someone",
        ),
    )

    op.create_index("ix_deployments_tenant_id", _TABLE, ["tenant_id"])
    op.create_index("ix_deployments_project_id", _TABLE, ["project_id"])
    op.create_index("ix_deployments_run_id", _TABLE, ["run_id"])
    # The pending queue — what the approval UI asks for on every load.
    op.create_index(
        "ix_deployments_pending", _TABLE, ["project_id", "approval_status"],
        postgresql_where=sa.text("approval_status = 'pending'"),
    )

    # Tenant isolation. `app.current_tenant_id` — see the module docstring.
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
    op.drop_index("ix_deployments_pending", table_name=_TABLE)
    op.drop_index("ix_deployments_run_id", table_name=_TABLE)
    op.drop_index("ix_deployments_project_id", table_name=_TABLE)
    op.drop_index("ix_deployments_tenant_id", table_name=_TABLE)
    op.drop_table(_TABLE)
