"""Move the read/write decision from the unit grant down to the project stage.

WHAT CHANGED AND WHY. `integration_grants.access` was a CEILING: the organisation
said a Business Unit had Jira at read-only, and nothing under that unit could ever
write. That is a coherent delegation model, but it is not the one this product
wants — the operative question is "may the QA agent write to Jira?", and that is a
delivery decision belonging to whoever designs the project's stages, not a governance
decision belonging to an Org Admin two levels up.

So the grant becomes a REACH decision only — may this unit use Jira at all, yes or
no — and the level moves to `projects.tool_access_modes`, keyed per (stage, tool) so
one connector can be read-only for QA and read-write for Development.

WHAT THIS GIVES UP, DELIBERATELY. There is no longer any ceiling above the project on
the read/write axis: whoever may administer a project may give its agents write access
to any connector the unit was granted, and no Org Admin setting can bound that. The
unit grant remains the kill switch — revoking it stops every stage at once — but it no
longer says HOW. This was chosen knowingly; see the module docstring on
shared/authz/connector_grants.py for how the resolver reads afterwards.

`project_connector_access` is kept and keeps its meaning as the project-wide default
for a connector. It is now the FALLBACK a stage with no explicit mode lands on, which
gives the two tables non-overlapping jobs instead of two ways to say one thing.

Revision ID: 0024_per_stage_tool_access
Revises: 0023_connector_access_level
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0024_per_stage_tool_access"
down_revision = "0023_connector_access_level"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keyed "{agent_id}::{connector|mcp}::{target_ref}" -> "read" | "write" | "both",
    # matching accessModeKey() in frontend/components/app/tools-stage-picker.tsx.
    #
    # JSONB on `projects` rather than a table of its own, because it is shaped and
    # written exactly like the `mcp_servers` and `connectors` maps beside it: one blob
    # chosen at creation and replaced wholesale on edit. A table would need its own RLS
    # policies and grants to hold data with no independent lifetime.
    op.add_column(
        "projects",
        sa.Column("tool_access_modes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # The ceiling is gone. Dropped rather than left in place unread: a NOT NULL column
    # that no reader consults is indistinguishable from one that still governs, and the
    # next person to find it would reasonably assume grants still carry a level.
    op.drop_constraint("ck_integration_grant_access", "integration_grants", type_="check")
    op.drop_column("integration_grants", "access")


def downgrade() -> None:
    # Restores the ceiling at its widest. read_write rather than the old `read` default
    # because narrowing on the way BACK would revoke write access that projects are, by
    # then, actively using — the same reason 0023 backfilled to read_write going forward.
    op.add_column(
        "integration_grants",
        sa.Column("access", sa.String(length=16), nullable=False, server_default="read_write"),
    )
    op.create_check_constraint(
        "ck_integration_grant_access",
        "integration_grants",
        "access IN ('read', 'write', 'read_write')",
    )
    op.alter_column("integration_grants", "access", server_default=None)
    op.drop_column("projects", "tool_access_modes")
