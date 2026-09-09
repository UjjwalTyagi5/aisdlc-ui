"""Record whether a consumption was an owner-granted exception.

`artifact_consumptions` says WHICH version a run read. It did not say HOW the read was
allowed, and those are different questions the moment anything goes wrong:

  published   the owning role signed it off; every consumer may read it
  granted     nothing was published, and an owner allowed THIS consumer THIS version
              as a one-off

The evidence view exists to answer "what did this run build on", and an exception
listed identically to routine approved work is the failure this whole workstream keeps
circling — an alarm that never distinguishes itself stops being read. `UpstreamRead`
already carried `via_grant` for the caller; it was simply never persisted, so the
distinction survived exactly as long as the request that made it.

DEFAULT FALSE, NOT NULL. Rows written before this column existed were reads of
published versions — enforcement was on and grants did not exist yet — so false is the
truthful backfill rather than a convenient one.

Revision ID: 0050_consumption_via_grant

Revises: 0049_consumption_type
"""
import sqlalchemy as sa
from alembic import op

revision = "0050_consumption_via_grant"
down_revision = "0049_consumption_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "artifact_consumptions",
        sa.Column("via_grant", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
    )
    # The auditor's filter: "show me every exception on this project".
    op.create_index(
        "ix_artifact_consumptions_exceptions", "artifact_consumptions",
        ["project_id", sa.text("consumed_at DESC")],
        postgresql_where=sa.text("via_grant"),
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_consumptions_exceptions",
                  table_name="artifact_consumptions")
    op.drop_column("artifact_consumptions", "via_grant")
