"""merge — unify the two 0030 heads (model_limits + pipeline_artifacts).

Revision ID: 0031_merge_model_limits_pipeline
Revises: 0030_model_limits, 0030_pipeline_artifacts

Pure merge revision: both 0030_* branched off the 0029 pair independently (model
limits vs. pipeline artifacts), leaving two heads after the sdlc_product merge.
No schema operations — just re-joins the graph to a single head.
"""

revision = "0031_merge_model_limits_pipeline"
down_revision = ("0030_model_limits", "0030_pipeline_artifacts")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
