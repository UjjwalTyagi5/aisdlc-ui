"""A note from whoever put a document forward.

An approver looking at the queue sees a filename, a stage and a person. What they do not
see is WHY — "this replaces the draft Ana rejected", "pages 4-6 are the only change",
"raising this for sign-off before Thursday". That context existed only in whatever
conversation happened around the upload, so the approver either guessed or went asking.

NULLABLE, AND IT STAYS NULLABLE. Requiring a note would put a box in front of every
upload and get "." typed into it, which is worse than nothing: an empty note is honest,
a meaningless one is noise the approver still has to read. Documents produced by an
agent carry one only when the agent has something specific to say.

TEXT, NOT VARCHAR(n). There is no length this should refuse at the database — the API
caps it at 2000 characters, which is where a limit belongs, because a rejected upload
over a note length would lose the file too.

Revision ID: 0056_artifact_upload_note
Revises: 0055_artifact_delete
"""
from alembic import op
import sqlalchemy as sa

revision = "0056_artifact_upload_note"
down_revision = "0055_artifact_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("artifacts", sa.Column("upload_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("artifacts", "upload_note")
