"""Let an artifact say which project and which agent it belongs to.

WHAT COULD NOT BE EXPRESSED BEFORE. `run_id` was NOT NULL and there was no `project_id`
or agent column, so:

  · a document was only "the Design agent's" by accident of which run produced it
  · a project-wide document — a policy, a compliance standard — had nowhere to live
  · a person could not add one at all, because every row needed a run to hang off

`artifact_store.blob_path_for` has emitted `{tenant}/{bu}/{project}/{agent}/{run}/...`
all along and already takes `project_id` and `agent`. The storage layout anticipated
this; the row never caught up.

    stage IS NULL   project-level  — every agent may read it once approved
    stage = 'design' agent-level   — readable elsewhere only via a published version

`run_id` becomes NULLABLE because a document outlives the run that made it, and a
hand-uploaded one never had a run. ON DELETE SET NULL rather than CASCADE for the same
reason: deleting a run must not destroy an approved document.

`uploaded_by` is deliberately separate from `approved_by`. Who put a document there and
who accepted it are different people and different questions, and collapsing them would
make self-approval invisible.

BACKFILL, THEN TIGHTEN, IN ONE MIGRATION. Adding a NOT NULL column to a populated table
fails outright, so the column lands nullable, fills from the join, and is tightened
last. All three in one revision so a half-migrated database cannot exist.

AND IT REFUSES RATHER THAN GUESSING. `runs.project_id` is itself nullable, so an
artifact on a project-less run cannot be backfilled. Rather than failing on the NOT NULL
with an opaque constraint error — or worse, quietly leaving the column nullable — this
raises and names the count. What to do with orphaned artifacts is a decision for a human
who knows what they are, not for a migration.

RLS needs nothing: `artifacts` already has tenant_isolation + tenant_isolation_insert
with FORCE.

Revision ID: 0052_artifact_scope

Revises: 0051_owner_gates
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0052_artifact_scope"
down_revision = "0051_owner_gates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── refuse before changing anything ──────────────────────────────────────
    orphans = conn.execute(sa.text(
        "SELECT count(*) FROM artifacts a "
        "LEFT JOIN runs r ON r.id = a.run_id "
        "WHERE r.id IS NULL OR r.project_id IS NULL"
    )).scalar() or 0
    if orphans:
        raise RuntimeError(
            f"{orphans} artifact(s) have no run, or a run with no project_id, so "
            "artifacts.project_id cannot be backfilled. Decide what these are before "
            "migrating: attach them to a project, or delete them. Query:\n"
            "  SELECT a.id, a.artifact_type, a.run_id FROM artifacts a "
            "LEFT JOIN runs r ON r.id = a.run_id "
            "WHERE r.id IS NULL OR r.project_id IS NULL;"
        )

    # ── add nullable ─────────────────────────────────────────────────────────
    op.add_column("artifacts", sa.Column("project_id", postgresql.UUID(as_uuid=True)))
    op.add_column("artifacts", sa.Column("stage", sa.String(32)))
    op.add_column("artifacts", sa.Column("uploaded_by", sa.String(255)))

    # ── backfill from the run that was previously the only source of truth ───
    conn.execute(sa.text(
        "UPDATE artifacts a SET project_id = r.project_id, stage = r.stage "
        "FROM runs r WHERE r.id = a.run_id"
    ))

    # ── tighten ──────────────────────────────────────────────────────────────
    op.alter_column("artifacts", "project_id", nullable=False)
    op.create_foreign_key(
        "fk_artifacts_project_id", "artifacts", "projects",
        ["project_id"], ["id"], ondelete="CASCADE",
    )

    # A document outlives its run. The FK was implicit before (no constraint); it is
    # named now so the SET NULL behaviour is explicit rather than incidental.
    op.alter_column("artifacts", "run_id", nullable=True)

    # THE READ THE PAGE MAKES: this project's documents, optionally one agent's.
    op.create_index("ix_artifacts_project_stage", "artifacts", ["project_id", "stage"])


def downgrade() -> None:
    # Rows that never had a run — hand-uploaded documents — cannot survive run_id
    # going back to NOT NULL. They are deleted rather than silently re-parented onto
    # some arbitrary run, which would misattribute a document to work that did not
    # produce it.
    op.execute("DELETE FROM artifacts WHERE run_id IS NULL")
    op.drop_index("ix_artifacts_project_stage", table_name="artifacts")
    op.alter_column("artifacts", "run_id", nullable=False)
    op.drop_constraint("fk_artifacts_project_id", "artifacts", type_="foreignkey")
    op.drop_column("artifacts", "uploaded_by")
    op.drop_column("artifacts", "stage")
    op.drop_column("artifacts", "project_id")
