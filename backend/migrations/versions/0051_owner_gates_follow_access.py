"""Give the Development and Documentation owners the gates they now own.

`frontend/lib/roles.ts` ("One agent, one role") moved two agent owners:

    development    architect     -> developer
    documentation  project_admin -> ba

because a delivery role now reaches exactly the agents it OWNS, and leaving a gate
with a role that can no longer open the agent routes every sign-off to somebody who
cannot act on it.

THE FRONTEND CHANGE SHIPPED WITHOUT THIS. `artifact:approve_development` was held by
`architect` alone and `artifact:approve_documentation` by `project_admin` alone, so
the product named an owner who could not approve — the same defect 0044 closed for
`code_review` and `plan`, arriving by a different route. Caught by
`tests/test_agent_ownership_is_single_sourced.py`, which compares the owner map
against the permission holders rather than against expected values.

ADDITIVE ONLY. `architect` keeps `artifact:approve_development` and `project_admin`
keeps `artifact:approve_documentation`: the ownership invariant needs the OWNER to
hold the permission, not to hold it exclusively, and project_admin is the documented
fallback approver on every agent. Removing them would be a second, larger decision.

`governance:decide` for `developer` follows 0037 and 0044 exactly: stage two of an
`agent_access` request for Development now routes to this role, and
`POST /governance-approvals/{id}/decide` is gated on that permission with no per-type
carve-out. `ba` already holds it.

WHY A MIGRATION. `role_permissions` is code-owned and reconciled from
`_ROLE_PERMISSIONS` on boot, but `assert_rbac_catalog` VERIFIES before seeding and
raises `RbacCatalogDriftError` on any difference. A code-matrix change without its
data migration makes every existing database refuse to start.

Revision ID: 0051_owner_gates

Revises: 0050_consumption_via_grant
"""
from alembic import op

revision = "0051_owner_gates"
down_revision = "0050_consumption_via_grant"
branch_labels = None
depends_on = None

_GRANTS = (
    ("developer", "artifact:approve_development"),
    ("developer", "governance:decide"),
    ("ba", "artifact:approve_documentation"),
)


def upgrade() -> None:
    for role, permission in _GRANTS:
        op.execute(
            "INSERT INTO role_permissions (role_name, permission_name) "
            "VALUES ('%s', '%s') ON CONFLICT (role_name, permission_name) DO NOTHING"
            % (role, permission)
        )


def downgrade() -> None:
    for role, permission in _GRANTS:
        op.execute(
            "DELETE FROM role_permissions WHERE role_name = '%s' "
            "AND permission_name = '%s'" % (role, permission)
        )
