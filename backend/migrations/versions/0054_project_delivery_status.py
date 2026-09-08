"""Where a project stands as delivery work — the human-set status.

WHAT WAS BROKEN. The Overview page has had a delivery-status picker for a while:
"Not started / In progress / On hold / Completed", editable by a Project, Business Unit
or Organization Admin. It was built against the mock fixtures — `lib/mock/
project-fixtures.ts` applies the patch to an in-memory object — and never carried to
the real API. There was no column, no ORM field, no `ProjectOut` field and nothing in
`ProjectPatchIn`.

The failure mode was the worst-behaved kind. `PATCH /projects/{id}` accepted
`{"deliveryStatus": "in_progress"}` and answered **200**, because FastAPI drops fields
the model does not declare. The response then carried no `deliveryStatus`, so the
frontend's `ProjectDeliveryStatus.default("not_started")` filled one in, and the success
toast read "Status set to Not started" — a confirmation of something that had not
happened, reporting the wrong value. The pill snapped back on the next refetch and the
control looked simply dead.

WHY A CHECK CONSTRAINT AND NOT A FREE VARCHAR. The same four values exist in
`lib/schemas/project.ts`, and a status the UI offers but the database refuses is exactly
the drift `test_db_enums_match_the_code.py` was written for — twice in one afternoon a
value list lived in Python and in a CHECK and disagreed, and a refused INSERT aborts the
whole Postgres transaction, so it surfaces as an unrelated error several frames away.
`_DELIVERY_STATUSES` in shared/routers/projects.py is the code half; this is the other.

NOT NULL WITH A DEFAULT, so every existing project reads "not started" rather than null.
The frontend already defaults a missing value that way, and a nullable column would mean
two spellings of the same fact.

Revision ID: 0054_delivery_status

(Under 32 characters: `alembic_version.version_num` is varchar(32), and a longer id
fails at the very END — every statement succeeds, then the version stamp raises
StringDataRightTruncation and the whole transaction rolls back. See 0037.)

Revises: 0053_doc_consumption
"""
import sqlalchemy as sa
from alembic import op

revision = "0054_delivery_status"
down_revision = "0053_doc_consumption"
branch_labels = None
depends_on = None

_VALUES = ("not_started", "in_progress", "on_hold", "completed")


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "delivery_status",
            sa.String(length=32),
            nullable=False,
            server_default="not_started",
        ),
    )
    op.create_check_constraint(
        "ck_project_delivery_status",
        "projects",
        sa.text(
            "delivery_status IN ("
            + ", ".join(f"'{v}'" for v in _VALUES)
            + ")"
        ),
    )


def downgrade() -> None:
    op.drop_constraint("ck_project_delivery_status", "projects", type_="check")
    op.drop_column("projects", "delivery_status")
