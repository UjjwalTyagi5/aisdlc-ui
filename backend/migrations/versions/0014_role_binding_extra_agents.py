"""Per-membership extra agent access.

A project roster can give one person agents their ROLE does not already reach — a QA
who also needs the Security agent on this project, without making that true of every
QA everywhere.

ADDITIVE ONLY, and the column shape is what keeps it that way: it lists agents to ADD,
never ones to remove. A subtractive override would make "what does a BA reach" a
question you cannot answer without inspecting every membership row in the tenant, and
the role matrix would stop being a description of anything.

Lives on `role_bindings` rather than in its own table because it is an attribute of one
binding — the grant and the extra reach are created, changed and revoked together, and
a separate table would only add a way for them to disagree. It is meaningful for
project-scope bindings; NULL everywhere else.

Revision ID: 0014_role_binding_extra_agents
Revises: 0013_notifications
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_role_binding_extra_agents"
down_revision = "0013_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "role_bindings",
        sa.Column("extra_agents", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("role_bindings", "extra_agents")
