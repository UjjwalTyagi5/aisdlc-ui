"""Approval requests — a stored decision with an initiator.

Approvals until now were DERIVED from `runs.gate_pending`: a run either was paused or
was not. That works for a pipeline gate but cannot express a request, because it has
no initiator, no id to act on, and no record of who decided. Self-approval could not
even be described, let alone blocked — there was nothing to compare a caller against.

`initiator_id` is the column that makes the rule possible, and it is NOT NULL: a
request with no initiator would silently bypass the self-approval check, which is the
one thing this table exists to make enforceable.

`target_role` records who SHOULD decide. Nothing substitutes a different approver when
nobody holds it — a request that cannot be actioned stays pending and visible. The
`fallback_used` column added here is dropped again in 0008; see that migration for why.

Revision ID: 0007_approval_requests
Revises: 0006_audit_query_index
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_approval_requests"
down_revision = "0006_audit_query_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Who raised it. NOT NULL: the self-approval check compares against this, and a
        # nullable column would make "no initiator" a way around the rule.
        sa.Column("initiator_id", sa.String(length=255), nullable=False),
        # What is being approved — deliberately loose (kind + id) rather than an FK per
        # subject type, because a request may concern a run, a project, a model grant or
        # a role assignment, and no single FK spans those.
        sa.Column("subject_kind", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        # Who SHOULD decide, and where they must hold that role.
        sa.Column("target_role", sa.String(length=64), nullable=False),
        sa.Column("scope_kind", sa.String(length=32), nullable=False),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Classifies the request. It does NOT select an approver: see migration 0008,
        # which drops fallback_used — nothing substitutes a decider when no one holds
        # target_role.
        sa.Column(
            "request_type", sa.String(length=32), nullable=False, server_default="standard"
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column(
            "fallback_used", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name="ck_approval_request_status",
        ),
        sa.CheckConstraint(
            "request_type IN ('standard', 'specialist_required')",
            name="ck_approval_request_type",
        ),
        sa.CheckConstraint(
            "scope_kind IN ('organization', 'business_unit', 'project', 'workstream')",
            name="ck_approval_request_scope_kind",
        ),
        # A decided request must say who decided it and when; a pending one must not.
        # Expressed as a constraint rather than trusted to the service layer because a
        # decision with no decider is exactly the row an audit would ask about.
        sa.CheckConstraint(
            "(status = 'pending' AND decided_by IS NULL AND decided_at IS NULL) "
            "OR (status <> 'pending' AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_approval_request_decision_complete",
        ),
    )
    op.create_index(
        op.f("ix_approval_requests_tenant_id"), "approval_requests", ["tenant_id"]
    )
    op.create_index(
        "ix_approval_requests_queue",
        "approval_requests",
        ["tenant_id", "status", "created_at"],
    )

    op.execute("ALTER TABLE approval_requests ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON approval_requests "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_insert ON approval_requests "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute("ALTER TABLE approval_requests FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                GRANT SELECT, INSERT, UPDATE ON approval_requests TO sdlc_app;
                -- No DELETE: a request is cancelled, never erased. The decision trail
                -- is the reason the table exists.
                REVOKE DELETE ON approval_requests FROM sdlc_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON approval_requests")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON approval_requests")
    op.drop_index("ix_approval_requests_queue", table_name="approval_requests")
    op.drop_index(op.f("ix_approval_requests_tenant_id"), table_name="approval_requests")
    op.drop_table("approval_requests")
