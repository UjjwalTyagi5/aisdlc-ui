"""Allow `artifact_consumption` as a governance request type.

`governance_requests.type` is CHECK-constrained to the catalogue in
`shared/governance/routing.py::REQUEST_TYPES`. That is the right shape — a typo'd type
would sit in a queue nothing routes — but it means adding a type is TWO changes, and
Python is only one of them.

WHAT THIS COST BEFORE IT EXISTED. The type was registered, the approver resolved
correctly to `architect`, the payload was assembled correctly — and the INSERT was
refused by the database. Worse than a clean failure: the aborted transaction then made
every later statement in the same request fail, so the visible symptom was an unrelated
RLS error on `artifact_versions` and a string of 404s. The second copy of a list is
always the one that gets forgotten.

NOTHING PINNED THE TWO LISTS TOGETHER, which is why this was possible.
`test_governance_requests.py` compares the Python maps to EACH OTHER — labels against
types, approvers against types — and never against the database. A drift test now does
(`tests/test_db_enums_match_the_code.py`), for this constraint and for
`ck_notification_kind`, which had the identical gap.

Revision ID: 0049_consumption_type

Revises: 0048_superseded_kind
"""
from alembic import op

revision = "0049_consumption_type"
down_revision = "0048_superseded_kind"
branch_labels = None
depends_on = None

_TYPES = (
    "project_creation", "model_credential", "budget_increase", "project_archive",
    "project_settings_change", "agent_default_org", "agent_default_workspace",
    "agent_default_project", "connector_access", "mcp_server", "agent_access",
    "access_request", "user_onboarding", "role_assignment", "cross_bu_assignment",
    "model_provider_access", "other",
)
_NEW = "artifact_consumption"


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
    # Requests of the new type would violate the narrower constraint. They are
    # DELETED rather than rewritten to another type: a consumption request relabelled
    # as `other` would sit in somebody's queue meaning nothing, and the grants any of
    # them produced survive independently in `artifact_consumption_grants`.
    op.execute(f"DELETE FROM governance_requests WHERE type = '{_NEW}'")
    _rewrite(_TYPES)
