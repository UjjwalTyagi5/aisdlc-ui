"""Model call guardrails — per-call cost cap on model_providers.

Task #20/#22 remainder (docs/superpowers/specs/2026-08-11-model-gateway-bu-cascade-design.md
§8.3): the org-level guardrail named in both the task list and the PRD
("organisation-wide guardrails such as max cost per call", PRD §376) — distinct
from model_offerings.cost_limit_usd, which is the existing *monthly* per-model
budget enforced in model_rate_limit.enforce_cost and stays unchanged.

A single column, not a `guardrails_json` blob: this codebase's existing convention
for provider/offering guardrails is discrete typed columns (rpm_limit, tpm_limit,
cost_limit_usd on model_offerings) rather than a loose JSON bag — matching that
convention keeps this guardrail queryable/indexable the same way the others are.

Revision ID: 0020_model_call_guardrails
Revises: 0019_governance_decide
"""
import sqlalchemy as sa
from alembic import op

revision = "0020_model_call_guardrails"
down_revision = "0019_governance_decide"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_providers",
        sa.Column("max_cost_per_call_usd", sa.Numeric(10, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("model_providers", "max_cost_per_call_usd")
