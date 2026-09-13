"""Bind each business unit to its own Langfuse organization, by id.

BU = Langfuse organization (0057 established project = Langfuse project inside it). Until
now that organization was created lazily — on the first traced agent turn — and found
again by NAME. Both are problems this migration is the first half of fixing.

WHY BY ID. Langfuse does not make organization names unique, and this instance is shared
with a sibling product: it already contains organizations called `Payments` and `Lending`
that belong to someone else. Name matching means a unit called `Payments` adopts theirs
and writes this platform's traces into an organization their users can read — precisely
the cross-unit mixing 0057 exists to prevent. Recording the id we minted ourselves lets
provisioning refuse to adopt an organization it did not create, and lets rename, membership
and teardown address the right one with no ambiguity.

NULL IS A REAL STATE, not a defect. Every Langfuse call in the BU lifecycle is fail-soft —
an unreachable Langfuse must never stop somebody creating a business unit — so a unit can
exist before its organization does. Units created before this migration are all in that
state. `backend/scripts/sync_langfuse_orgs.py` converges them.

NO RLS STATEMENTS HERE, deliberately, unlike 0057. `workspaces` is a baseline table that
has carried FORCE ROW LEVEL SECURITY and its tenant policy since 0001; adding columns does
not change that, and re-applying the policy here would be the second definition of one
rule. 0057 needed its own four statements only because it created a new table.

Revision ID: 0058_workspace_langfuse_org
Revises: 0057_langfuse_bindings
"""
from alembic import op
import sqlalchemy as sa

revision = "0058_workspace_langfuse_org"
down_revision = "0057_langfuse_bindings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspaces", sa.Column("langfuse_org_id", sa.String(64), nullable=True))
    op.add_column("workspaces", sa.Column("langfuse_org_name", sa.String(255), nullable=True))
    # Not unique: a Langfuse rebuild can legitimately leave two units pointing at the same
    # organization until the reconciler runs, and a constraint that fails at that moment
    # would block editing a business unit for a reason the operator cannot act on. The
    # reconciler reports the collision instead.
    op.create_index(
        "ix_workspaces_langfuse_org", "workspaces", ["langfuse_org_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_workspaces_langfuse_org", table_name="workspaces")
    op.drop_column("workspaces", "langfuse_org_name")
    op.drop_column("workspaces", "langfuse_org_id")
