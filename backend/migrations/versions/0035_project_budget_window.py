"""Add projects.budget_start_date / budget_end_date.

A project's budget is a lifetime total (see shared/services/budget_store.py); these
say for how long that total is authorised — a funded phase of work with an end.

THE FRONTEND HAS SENT THESE ALL ALONG. `frontend/lib/schemas/budget-window.ts` has
defined the pair, its state machine and its validator for some time, the create
dialog collects them, and `lib/api/projects.ts` puts them on the wire. With no
field on ProjectCreateIn, Pydantic dropped them silently: two dates typed into a
form, submitted, and gone. This is the column they were always meant to land in.

DATE, not timestamp. A budget window is a business period somebody types into a
form ("this runs to the end of Q3"), and an instant would invite a timezone
question with no useful answer — the same reasoning the frontend schema gives for
storing `YYYY-MM-DD` strings.

Nullable, and null is the ordinary case: a project with no window is the common
one, and NULL already reads as "no bound" throughout the frontend helpers. Both
ends are independently optional — a start with no end, or an end with no start,
are both meaningful.

Projects only. The workspace create dialog renders the same inputs and still
discards them; closing that is separate work rather than a column added here on
spec.

Revision ID: 0035_project_budget_window
Revises: 0034_settings_change_request
"""
from alembic import op
import sqlalchemy as sa

# 25 chars. alembic_version.version_num is varchar(32), and a longer id fails the
# stamp UPDATE *after* the DDL has already run — see 0034's own note.
revision = "0035_project_budget_window"
down_revision = "0034_settings_change_request"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("budget_start_date", sa.Date(), nullable=True))
    op.add_column("projects", sa.Column("budget_end_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "budget_end_date")
    op.drop_column("projects", "budget_start_date")
