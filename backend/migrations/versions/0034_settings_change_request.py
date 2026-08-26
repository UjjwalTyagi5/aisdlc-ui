"""Allow governance_requests to carry a project_settings_change.

A Project Admin editing their own project's settings no longer writes them
directly — the edit becomes a request their Business Unit Admin decides, and the
values are applied on approval (shared/governance/effects.py). The type has to be
admissible in the table before any of that can be filed.

The CHECK constraint from 0010 enumerates every request type, so adding one is a
migration rather than a code-only change. Same shape as
0028_model_provider_grant_kind did for integration_grants.kind: drop and recreate
with the new member, since Postgres cannot extend a CHECK in place.

Revision ID: 0034_settings_change_request
Revises: 0033_project_integration_config
"""
from alembic import op

# 28 chars: alembic_version.version_num is varchar(32), so a longer id fails
# the stamp UPDATE *after* the DDL has run.
revision = "0034_settings_change_request"
down_revision = "0033_project_integration_config"
branch_labels = None
depends_on = None

# Mirrors shared/governance/routing.py::REQUEST_TYPES. The two must agree — the
# service validates against that tuple and the database enforces this list, so a
# type present in one and absent from the other is either a 422 nobody expects or
# a CheckViolation at insert.
_TYPES = (
    "project_creation",
    "model_credential",
    "budget_increase",
    "project_archive",
    "project_settings_change",
    "agent_default_org",
    "agent_default_workspace",
    "agent_default_project",
    "connector_access",
    "mcp_server",
    "agent_access",
    "access_request",
    "user_onboarding",
    "role_assignment",
    "cross_bu_assignment",
    "model_provider_access",
    "other",
)

_PREVIOUS = tuple(t for t in _TYPES if t != "project_settings_change")


def _recreate(types: tuple[str, ...]) -> None:
    values = ", ".join(f"'{t}'" for t in types)
    op.execute("ALTER TABLE governance_requests DROP CONSTRAINT ck_governance_request_type")
    op.execute(
        "ALTER TABLE governance_requests ADD CONSTRAINT ck_governance_request_type "
        f"CHECK (type IN ({values}))"
    )


def upgrade() -> None:
    _recreate(_TYPES)


def downgrade() -> None:
    # Rows of the new type would violate the narrower constraint, so they go first.
    # Deleting rather than remapping: a settings-change request has no meaning under
    # any other type, and inventing one would leave an approver a request whose
    # payload does not match what its type claims.
    op.execute("DELETE FROM governance_requests WHERE type = 'project_settings_change'")
    _recreate(_PREVIOUS)
