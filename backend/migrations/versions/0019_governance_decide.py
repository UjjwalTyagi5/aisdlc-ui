"""Add `governance:decide` and grant it to the two approver tiers.

The governance request lane — raise, route upward, decide — authorised decisions by
ROLE-STRING MATCHING alone: `decider_role != request["currentApproverRole"]` in
`shared/services/governance_requests.py`. There was no permission for it anywhere, so
the whole lane sat behind the router's `artifact:view` floor and, more to the point, a
tenant-defined custom role could be neither granted nor denied governance decisioning.
Being able to express exactly that is what custom roles are for.

The permission does NOT replace the routing check; both apply. `governance:decide` says
this role takes governance decisions at all, `routing.REQUEST_ESCALATION_CHAIN` says
whose turn it is. Granted to the two non-wildcard tiers of that chain:

    project_admin  tier 1 — the first approver a request reaches
    bu_admin       tier 2
    org_admin      tier 3, reached via admin:* and so not listed here

Cancel and escalate deliberately get no permission gate — see the route comments. Both
are open to the person who RAISED the request, who is usually a delivery role.

WHY A MIGRATION. `role_permissions` is code-owned and reconciled from
`_ROLE_PERMISSIONS` on boot, but `assert_rbac_catalog` verifies BEFORE seeding and
raises `RbacCatalogDriftError` on any difference, self-seeding only when `roles` is
empty. A code-matrix change without its data migration therefore makes an existing
database refuse to start. Alembic runs before app start, so these rows exist by the
time the guard looks. Same reasoning as 0018.

Unlike 0018, this one inserts the PERMISSION row as well: `governance:decide` is new to
the catalogue, and `role_permissions.permission_name` has an FK onto `permissions(name)`
that would fail without it.

Revision ID: 0019_governance_decide
Revises: 0018_grant_phase_approvals

(The id is kept short deliberately: `alembic_version.version_num` is varchar(32), and a
longer one fails at the very END of the migration — every statement succeeds, then the
version stamp raises StringDataRightTruncation and the whole transaction rolls back, so
the log reads "Running upgrade …" and nothing was applied.)
"""
from alembic import op

revision = "0019_governance_decide"
down_revision = "0018_grant_phase_approvals"
branch_labels = None
depends_on = None

_PERMISSION = "governance:decide"
_ROLES = ["project_admin", "bu_admin"]


def upgrade() -> None:
    op.execute(
        "INSERT INTO permissions (name) VALUES ('%s') ON CONFLICT (name) DO NOTHING"
        % _PERMISSION
    )
    for role in _ROLES:
        op.execute(
            "INSERT INTO role_permissions (role_name, permission_name) "
            "VALUES ('%s', '%s') ON CONFLICT (role_name, permission_name) DO NOTHING"
            % (role, _PERMISSION)
        )


def downgrade() -> None:
    # Edges first — the FK on role_permissions.permission_name would refuse the
    # permission delete while any grant still references it.
    for role in _ROLES:
        op.execute(
            "DELETE FROM role_permissions WHERE role_name = '%s' AND permission_name = '%s'"
            % (role, _PERMISSION)
        )
    op.execute("DELETE FROM permissions WHERE name = '%s'" % _PERMISSION)
