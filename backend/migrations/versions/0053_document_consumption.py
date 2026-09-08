"""Let the evidence trail record a DOCUMENT read, not only a version read.

`artifact_consumptions` answers "what did this run build on". It could only ever point
at an `artifact_versions` row, because `version_id` was NOT NULL — so once agents can
read documents there were two reads it could not record:

  · a PROJECT-LEVEL document, which has no stage and therefore no version to cover it
  · a document fetched on its own by `read_document`, after the upstream read that
    listed it

Both would have gone unrecorded, which is the one thing this table exists to prevent.
An evidence view with a hole in it is worse than none: it reads as complete.

    version_id  NULL allowed   — a document read has no version
    artifact_id NULL allowed   — a version read has no single document
    CHECK       one of the two — a row pointing at neither records nothing

WHY NOT A SECOND TABLE. "What did this run build on" is one question, and answering it
from two tables means every caller unions them and one of them eventually forgets.

Revision ID: 0053_doc_consumption

Revises: 0052_artifact_scope
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0053_doc_consumption"
down_revision = "0052_artifact_scope"
branch_labels = None
depends_on = None

_TABLE = "artifact_consumptions"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_foreign_key(
        "fk_artifact_consumptions_artifact_id", _TABLE, "artifacts",
        ["artifact_id"], ["id"], ondelete="CASCADE",
    )
    op.alter_column(_TABLE, "version_id", nullable=True)

    # `producing_stage` and `version` were NOT NULL and denormalised from the version.
    # A project-level document has neither — it belongs to no stage by definition.
    op.alter_column(_TABLE, "producing_stage", nullable=True)
    op.alter_column(_TABLE, "version", nullable=True)

    op.create_check_constraint(
        "ck_artifact_consumptions_points_at_something", _TABLE,
        "version_id IS NOT NULL OR artifact_id IS NOT NULL",
    )
    op.create_index("ix_artifact_consumptions_artifact", _TABLE, ["artifact_id"])


def downgrade() -> None:
    # Document-only rows cannot survive version_id going back to NOT NULL. Deleting
    # them loses evidence, which is why the downgrade is the unpleasant direction —
    # but re-pointing them at some arbitrary version would be a lie in the audit trail,
    # and a lie is worse than a gap.
    op.execute(f"DELETE FROM {_TABLE} WHERE version_id IS NULL")
    op.drop_index("ix_artifact_consumptions_artifact", table_name=_TABLE)
    op.drop_constraint(
        "ck_artifact_consumptions_points_at_something", _TABLE, type_="check"
    )
    op.alter_column(_TABLE, "version", nullable=False)
    op.alter_column(_TABLE, "producing_stage", nullable=False)
    op.alter_column(_TABLE, "version_id", nullable=False)
    op.drop_constraint(
        "fk_artifact_consumptions_artifact_id", _TABLE, type_="foreignkey"
    )
    op.drop_column(_TABLE, "artifact_id")
