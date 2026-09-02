"""Record who started a run, so the gate can refuse to let them approve it.

§1.5 of `help/multi-track-agent-access-design.md`: *"whoever ran the agent is never the
one who accepts its own output."* That rule was enforced nowhere on the gate path, and
it could not have been: `_handle_gate_decision` checks `can_user_approve(perms, stage)`,
which answers "may this ROLE approve this stage" — a different question from "is this
the same PERSON who started the run". Nothing on `runs` recorded the initiator, so the
comparison had no left-hand side.

`approval_requests` already does this properly (`decide()` compares `initiator_id`
before anything else and raises INITIATOR_REQUIRED when it is unknown). This gives the
gate path the same column to compare against.

NULLABLE, AND DELIBERATELY SO. Two reasons, and the second is the important one:

  * Every run that already exists has no recorded initiator. A NOT NULL column would
    need a backfill value, and any value invented here would be a lie about who started
    those runs — one that the self-approval check would then act on.
  * Webhook-triggered runs (`trigger='webhook'`) genuinely have no human initiator.
    NULL is the truthful answer for them, not a defect to be filled in.

So NULL means "initiator unknown", and the check treats it as *not proven to be
self-approval* — it allows the decision and logs. That is a deliberate fail-open on a
narrow case, and it is the honest one: refusing instead would block approval on every
run created before this migration, and on every webhook run forever. The residual risk
is that a run created through a path that does not set `created_by` can be
self-approved; `tests/test_gate_self_approval.py` pins the paths that must set it so
that a new one cannot quietly join them.

VARCHAR(255) to match `conversation_sessions.created_by` and the other `created_by`
columns in this schema, which hold string user ids rather than a users FK.

Revision ID: 0038_run_created_by
Revises: 0037_agent_owner_decide
"""
import sqlalchemy as sa
from alembic import op

revision = "0038_run_created_by"
down_revision = "0037_agent_owner_decide"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("created_by", sa.String(length=255), nullable=True))


def downgrade() -> None:
    # Plain drop: the column is nullable with no default, no index and no constraint,
    # so nothing here validates against existing rows the way a NOT NULL or CHECK
    # would. (That failure mode is why 0026's downgrade had to order its statements.)
    op.drop_column("runs", "created_by")
