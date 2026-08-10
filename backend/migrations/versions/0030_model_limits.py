"""model_limits — per-model RPM / TPM / cost limits on model_offerings.

Revision ID: 0030_model_limits
Revises: 0029_conversation_agent_list, 0029_dev_workspaces

This revision ALSO merges the pre-existing two-head fork (both 0029_* branch off
0028_conversations) so the revision graph has a single head again.

Adds three nullable limit columns to model_offerings (per-model, tenant/workspace
scoped via the parent model_providers row):
  - rpm_limit       : requests per minute cap (enforced at resolve_model_for_run)
  - tpm_limit       : tokens per minute cap (stored; enforcement is a follow-up)
  - cost_limit_usd  : monthly USD budget for this model (stored; enforcement follow-up)

All nullable — NULL means "no limit", so existing offerings are unaffected.
"""
from alembic import op
import sqlalchemy as sa

# Tuple down_revision merges the forked heads.
revision = "0030_model_limits"
down_revision = ("0029_conversation_agent_list", "0029_dev_workspaces")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_offerings", sa.Column("rpm_limit", sa.Integer(), nullable=True))
    op.add_column("model_offerings", sa.Column("tpm_limit", sa.Integer(), nullable=True))
    op.add_column(
        "model_offerings", sa.Column("cost_limit_usd", sa.Numeric(12, 4), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("model_offerings", "cost_limit_usd")
    op.drop_column("model_offerings", "tpm_limit")
    op.drop_column("model_offerings", "rpm_limit")
