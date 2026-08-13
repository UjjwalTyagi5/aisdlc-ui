"""Governance requests — a person asking for something they cannot grant themselves.

WHY NOT `approval_requests`. That table already exists and is deliberately not reused.
It models a GATE: something an agent produced, paused for a human, with a target role
and one terminal decision. A governance request is the other lane in PRD §33.2 — raised
BY a person, routed UPWARD through tiers until one can grant it, and carrying the things
a gate has no use for: a type that says what is being asked, a priority, attachments, a
timeline, and for one type a second decision stage.

Squeezing those into `approval_requests` would have meant widening `request_type` from
two values to sixteen, adding five columns that are always NULL for gates, and relaxing
the status CHECK that currently makes "decided but no decider" unrepresentable. The two
lanes route differently on purpose, and a shared table is how they stop routing
differently.

TWO TABLES, AND THE SECOND IS APPEND-ONLY. `governance_request_events` is the audit
trail the request's own row cannot be: the row holds the CURRENT state, and a decision
history that can be tidied up afterwards is not a history. `sdlc_app` gets INSERT and
SELECT on it and nothing else — no UPDATE, no DELETE — so "who escalated this and when"
survives even a bug in the service layer.

`requested_by_id` is NOT NULL for the same reason `approval_requests.initiator_id` is:
the self-approval rule compares a decider against it, and a nullable column would make
"no initiator" the way around the rule.

Revision ID: 0010_governance_requests
Revises: 0009_org_settings
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010_governance_requests"
down_revision = "0009_org_settings"
branch_labels = None
depends_on = None


# Kept as literals rather than imported from shared.governance so the migration
# describes the schema at ITS point in history. A later code change that adds a
# type must add a migration to widen the constraint — which is the reminder.
REQUEST_TYPES = (
    "project_creation",
    "model_credential",
    "budget_increase",
    "project_archive",
    "agent_default_org",
    "agent_default_workspace",
    "agent_default_project",
    "connector_access",
    "mcp_server",
    "agent_access",
    "access_request",
    "user_onboarding",
    "role_assignment",
    "cross_bu_assignment",
    "model_provider_access",
    "other",
)

STATUSES = (
    "draft",
    "submitted",
    "pending_review",
    "approved",
    "rejected",
    "cancelled",
    "escalated",
)

# Still open, still someone's to answer. Mirrors OPEN_REQUEST_STATUSES in
# lib/schemas/governance-approval.ts.
OPEN_STATUSES = ("draft", "submitted", "pending_review", "escalated")

PRIORITIES = ("low", "normal", "high", "urgent")

EVENT_KINDS = (
    "created",
    "submitted",
    "assigned",
    "commented",
    "approved",
    "rejected",
    "escalated",
    "cancelled",
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "governance_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        # WHAT is being asked. Drives routing for the type-routed half of the
        # catalogue and drives what approving actually DOES.
        sa.Column("type", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="submitted"),
        # The unit the ask belongs to. NOT NULL even for org-wide asks: every
        # request is raised from somewhere, and the queue's scope filter needs a
        # unit to compare against.
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        # One line for the table row. `description` is the body; keeping them
        # apart is why the queue can stay scannable.
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("priority", sa.String(length=16), nullable=False, server_default="normal"),
        # Display name, for the row. The id below is what rules compare.
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column("requested_by_id", sa.String(length=255), nullable=False),
        # The role held WHEN RAISING. Frozen at creation on purpose: the upward
        # chain is computed from it, and someone whose role changes mid-flight
        # must not silently re-route a request already sitting in a queue.
        sa.Column("requested_by_role", sa.String(length=64), nullable=True),
        # Who holds it now. NULL once decided or cancelled — the queue reads
        # "waiting on nobody" from that rather than from the status alone.
        sa.Column("current_approver_role", sa.String(length=64), nullable=True),
        # Only `agent_access` uses this: it is answered twice, by two people
        # asking different questions. NULL for every single-decision type.
        sa.Column("approval_stage", sa.String(length=32), nullable=True),
        sa.Column(
            "escalation_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        # The project / provider / workspace / profile-version id the decision
        # applies to. A string rather than a UUID FK because the referent's TABLE
        # varies by type, and no single FK spans them.
        sa.Column("target_ref", sa.String(length=255), nullable=False),
        # Type-specific structured data the decide step needs — e.g.
        # {"requestedAmountUsd": 16000} for budget_increase. Exists so a new type
        # does not grow a new always-NULL column.
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        # Metadata only; there is no file behind these. See RequestAttachment in
        # lib/schemas/governance-approval.ts.
        sa.Column(
            "attachments",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
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
        sa.CheckConstraint(_in_list("type", REQUEST_TYPES), name="ck_governance_request_type"),
        sa.CheckConstraint(_in_list("status", STATUSES), name="ck_governance_request_status"),
        sa.CheckConstraint(
            _in_list("priority", PRIORITIES), name="ck_governance_request_priority"
        ),
        sa.CheckConstraint(
            "approval_stage IS NULL OR approval_stage IN ('project_admin', 'agent_owner')",
            name="ck_governance_request_stage",
        ),
        sa.CheckConstraint(
            "escalation_count >= 0", name="ck_governance_request_escalation_count"
        ),
        # A decided request names its decider and when. Enforced here rather than
        # trusted to the service, because a decision with no decider is precisely
        # the row an audit asks about. `cancelled` is excluded: it is the
        # initiator withdrawing, not somebody deciding, so it has no decider.
        sa.CheckConstraint(
            "(status IN ('approved', 'rejected') "
            "  AND decided_by IS NOT NULL AND decided_at IS NOT NULL) "
            "OR (status NOT IN ('approved', 'rejected') "
            "  AND decided_by IS NULL AND decided_at IS NULL)",
            name="ck_governance_request_decision_complete",
        ),
        # An open request is waiting on somebody; a closed one is waiting on
        # nobody. Without this, a rejected request could keep a
        # current_approver_role and go on showing in that role's queue forever.
        sa.CheckConstraint(
            f"({_in_list('status', OPEN_STATUSES)} AND current_approver_role IS NOT NULL) "
            f"OR (NOT {_in_list('status', OPEN_STATUSES)} AND current_approver_role IS NULL)",
            name="ck_governance_request_approver_when_open",
        ),
    )
    op.create_index(
        op.f("ix_governance_requests_tenant_id"), "governance_requests", ["tenant_id"]
    )
    # The queue's own read: this tenant's open requests, oldest first, because
    # SLA pressure rises to the top.
    op.create_index(
        "ix_governance_requests_queue",
        "governance_requests",
        ["tenant_id", "status", "created_at"],
    )
    # "Raised by me" and the self-approval lookup.
    op.create_index(
        "ix_governance_requests_initiator",
        "governance_requests",
        ["tenant_id", "requested_by_id"],
    )
    # The scope filter — which unit's requests may this viewer see.
    op.create_index(
        "ix_governance_requests_workspace",
        "governance_requests",
        ["tenant_id", "workspace_id"],
    )

    op.create_table(
        "governance_request_events",
        # A monotonic sequence, not just a timestamp: two events written in the
        # same transaction share `at` to the microsecond, and a timeline that
        # cannot order "escalated" against "assigned" tells the wrong story.
        sa.Column("seq", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        # Display name, or "System" for an automatic transition. Denormalised
        # deliberately: the trail must still read correctly after the person is
        # deactivated and their row stops resolving.
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("actor_role", sa.String(length=64), nullable=True),
        # The role it moved TO — `assigned` and `escalated` only. This is what
        # makes "why is a BU Admin deciding a project request" answerable from
        # the request itself.
        sa.Column("to_role", sa.String(length=64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("seq"),
        sa.UniqueConstraint("id", name="uq_governance_request_events_id"),
        sa.ForeignKeyConstraint(
            ["request_id"], ["governance_requests.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(_in_list("kind", EVENT_KINDS), name="ck_governance_event_kind"),
    )
    op.create_index(
        "ix_governance_request_events_request",
        "governance_request_events",
        ["request_id", "seq"],
    )
    op.create_index(
        op.f("ix_governance_request_events_tenant_id"),
        "governance_request_events",
        ["tenant_id"],
    )

    for table in ("governance_requests", "governance_request_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
        )
        op.execute(
            f"CREATE POLICY tenant_isolation_insert ON {table} "
            "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
        )
        # FORCE so the policy applies to the table owner too — without it, a
        # migration-role connection reads every tenant's rows.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sdlc_app') THEN
                GRANT SELECT, INSERT, UPDATE ON governance_requests TO sdlc_app;
                -- No DELETE: a request is cancelled, never erased.
                REVOKE DELETE ON governance_requests FROM sdlc_app;

                -- The trail is append-only. INSERT and SELECT only, so an event
                -- cannot be rewritten or removed even by a bug in the service.
                GRANT SELECT, INSERT ON governance_request_events TO sdlc_app;
                REVOKE UPDATE, DELETE ON governance_request_events FROM sdlc_app;
                GRANT USAGE, SELECT ON SEQUENCE governance_request_events_seq_seq TO sdlc_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    for table in ("governance_request_events", "governance_requests"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_insert ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_table("governance_request_events")
    op.drop_index("ix_governance_requests_workspace", table_name="governance_requests")
    op.drop_index("ix_governance_requests_initiator", table_name="governance_requests")
    op.drop_index("ix_governance_requests_queue", table_name="governance_requests")
    op.drop_index(op.f("ix_governance_requests_tenant_id"), table_name="governance_requests")
    op.drop_table("governance_requests")
