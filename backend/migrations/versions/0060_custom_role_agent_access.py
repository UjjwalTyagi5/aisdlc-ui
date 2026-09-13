"""Give custom_roles somewhere to keep the agent access the UI already collects.

The role composer has an AGENT ACCESS section -- a per-phase dropdown ("Primary",
"Build", "No access") -- and there was nowhere to put the answer. The BFF accepted
the field and dropped it on the floor, so a role created with Strategy=Primary came
back reading "No access" on every phase. Silent data loss: the form said it saved.

JSONB rather than a child table. The value is a small, closed map of phase -> level
that is always read and written whole, never queried across roles, and the row is
already fetched wherever it is needed. A join table would add a second write path
and a second thing to keep in step with the frontend Phase enum for no gain.

NULL means "never set", which is distinct from `{}` ("set, and nothing granted") --
worth keeping apart so a role composed before this migration is not mistaken for one
whose author deliberately cleared every phase.

Revision ID: 0060_custom_role_agent_access
Revises: 0059_merge_track3_langfuse
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0060_custom_role_agent_access"
down_revision = "0059_merge_track3_langfuse"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_roles",
        sa.Column("agent_access", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("custom_roles", "agent_access")
