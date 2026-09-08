"""`conversation_messages.agent_id` — WHICH agent said it.

The revision id is short on purpose: `alembic_version.version_num` is
varchar(32), and a longer one applies the DDL and then fails to record it.

REPORTED. A user worked with the Development agent across many turns — cloned a repo,
found a duplicate table, changed its colours to orange, pushed, opened a PR — then
asked the Requirements agent to write story tickets "for this change", and got:

    "I don't have any context about a 'change table to orange' modification from our
     conversation."

Two things caused that. The first is that `handoff_context` fed agents DELIVERABLES
only, so a conversation that produced no document handed the next agent an empty
string. The second is this column's absence: `role` is one of
`user | agent | orchestrator | system | tool`, so the transcript recorded that *an*
agent replied and never *which*. A transcript fed forward without it reads as one
undifferentiated voice, which is worse than useless when the whole point is "the
Development agent already did this".

NULLABLE, and null means "not attributable". Existing rows predate the column and
there is nothing to backfill them from — the information was never recorded. A
default of any particular agent id would invent attribution for turns nobody can
attribute, so the renderer treats null as an unnamed agent and says so.

NOT ONLY FOR AGENT ROWS. `user` turns carry null, but so does an `orchestrator` turn
where the router answered directly — that IS the Orchestrator speaking rather than one
of the nine, and conflating it with an unattributed agent turn would tell the next
agent that a delivery agent said something it did not.
"""
from alembic import op
import sqlalchemy as sa

revision = "0045_message_agent_id"
down_revision = "0044_orchestrator_deliverables"
branch_labels = None
depends_on = None

_TABLE = "conversation_messages"
_COLUMN = "agent_id"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        # 32 is comfortably above the longest of the nine (`documentation`, 13) and
        # matches the width `orchestrator_deliverables.agent_id` uses, so the two
        # tables agree about what an agent id is.
        sa.Column(_COLUMN, sa.String(length=32), nullable=True),
    )
    # Reading a run's transcript is `WHERE session_id = ? ORDER BY seq`, which the
    # existing `uq_message_session_seq` already serves. This column is projected, never
    # filtered on, so it gets no index of its own — an index nothing uses is a write
    # cost with no reader.


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN)
