"""Give the PM agent somewhere to write: runs.plan_artifacts.

PHASE 1 OF THE PM AGENT. Every stage writes its output into a JSONB column on the run —
requirements_payload, design_artifacts, development_artifacts and so on — and the next
stage reads it. The planner needs the same, and the column has to exist before the
registry entry that names it.

NULLABLE AND NO DEFAULT, matching every sibling column. A run that never reached the
planning stage genuinely has no plan, and NULL says that. An empty `{}` would mean "a
plan was produced and it is empty", which is a different and false claim — and
`derive_steps_from_run` reads exactly this distinction to decide whether the stage
happened at all.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0041_plan_artifacts"
down_revision = "0040_artifact_approval"
branch_labels = None
depends_on = None


# BOTH TABLES, because there are two artifact stores and each is read by a different
# path. `runs` is the project-scoped record every listing and downstream stage reads;
# `agent_sessions` is the session-scoped one `build_context` resolves for an
# orchestrated run. AgentSession is documented as a strict superset of Run's columns,
# and adding to only one would leave the planner invisible to whichever path was missed
# — the exact shape of the bug where the Requirements chat wrote a payload into
# agent_sessions that nothing on the project path ever consulted.
_TABLES = ("runs", "agent_sessions")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("plan_artifacts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "plan_artifacts")
