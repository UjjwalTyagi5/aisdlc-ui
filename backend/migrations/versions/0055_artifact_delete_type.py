"""Allow `artifact_delete` as a governance request type.

Deleting a document is now something an owner agrees to rather than something a holder
of `artifact:delete` simply does. Uploading was already gated on somebody accepting it;
removing was gated on nothing, so one click could undo an approval nobody was asked
about. This registers the request type that closes that asymmetry.

SECOND COPY OF A LIST, SAME AS 0049. `governance_requests.type` is CHECK-constrained to
`routing.REQUEST_TYPES`, so adding a type is two changes and Python is only one. When
0049's type was added to Python alone the INSERT was refused, the aborted transaction
made every later statement fail, and the visible symptom was an unrelated RLS error
several frames away. `tests/test_db_enums_match_the_code.py` now pins the two lists
together and covers this constraint.

Revision ID: 0055_artifact_delete
(Under 32 characters — see 0037 for what a longer one costs.)

Revises: 0054_delivery_status
"""
from alembic import op

revision = "0055_artifact_delete"
down_revision = "0054_delivery_status"
branch_labels = None
depends_on = None

_TYPES = (
    "project_creation", "model_credential", "budget_increase", "project_archive",
    "project_settings_change", "agent_default_org", "agent_default_workspace",
    "agent_default_project", "connector_access", "mcp_server", "agent_access",
    "access_request", "user_onboarding", "role_assignment", "cross_bu_assignment",
    "model_provider_access", "artifact_consumption", "other",
)
_NEW = "artifact_delete"


def _rewrite(types: tuple[str, ...]) -> None:
    values = ", ".join(f"'{t}'" for t in types)
    op.execute(
        "ALTER TABLE governance_requests "
        "DROP CONSTRAINT IF EXISTS ck_governance_request_type"
    )
    op.execute(
        "ALTER TABLE governance_requests ADD CONSTRAINT ck_governance_request_type "
        f"CHECK (type IN ({values}))"
    )


def upgrade() -> None:
    _rewrite(_TYPES + (_NEW,))


def downgrade() -> None:
    # DELETED rather than relabelled, as in 0049. A deletion request rewritten to
    # `other` would sit in somebody's queue naming a file with no way to act on it,
    # and any document already deleted by an approved one is gone regardless — the
    # request row is not what made it so.
    op.execute(f"DELETE FROM governance_requests WHERE type = '{_NEW}'")
    _rewrite(_TYPES)
