"""Rejoin two migration lines that both branched off 0056.

WHAT HAPPENED. Two branches numbered their first migration `0057` and both set
`down_revision = "0056_artifact_upload_note"`:

    0056_artifact_upload_note
       |-- 0057_track3_phase1_agents     (Track 3: runs.migration_intent_payload, .discovery_artifacts)
       `-- 0057_langfuse_bindings  -->  0058_workspace_langfuse_org   (Langfuse per-project isolation)

Sequential numbering makes that collision easy to miss in review — the files sort next
to each other and look like a sequence. Alembic is not fooled: it sees two heads and
`upgrade head` refuses to run at all ("Multiple head revisions are present"), so the
symptom is a deployment that cannot migrate rather than a schema that is subtly wrong.

WHY A MERGE REVISION RATHER THAN RENUMBERING. Renumbering looks tidier and is the
dangerous option: `0057_langfuse_bindings` and `0058_workspace_langfuse_org` are ALREADY
APPLIED, and `alembic_version` stores those exact strings. Rewriting a revision id that a
live database points at strands it — alembic can no longer find the revision the database
claims to be on, and every later command fails. A merge revision leaves both lines intact
and simply joins them.

WHAT THIS DOES TO A DATABASE ALREADY ON 0058. Nothing is dropped and nothing is re-run.
Both databases sit at `0058_workspace_langfuse_org` and never ran Track 3's migration, so
alembic treats it as an unapplied ancestor of this merge: `upgrade head` runs
`0057_track3_phase1_agents` (two nullable JSONB columns on `runs`, plus a check-constraint
swap), then stamps this revision, leaving one row in `alembic_version`.

There is deliberately nothing in `upgrade()`. A merge revision exists to join the graph;
putting schema changes here would hide them from anyone reading either branch's history.

Revision ID: 0059_merge_track3_langfuse
Revises: 0057_track3_phase1_agents, 0058_workspace_langfuse_org
"""

revision = "0059_merge_track3_langfuse"
down_revision = ("0057_track3_phase1_agents", "0058_workspace_langfuse_org")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: this revision joins two lines, it does not change the schema."""


def downgrade() -> None:
    """No-op: undoing the join splits the graph back into two heads."""
