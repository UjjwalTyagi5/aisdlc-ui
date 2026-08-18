"""Add `project:update` and grant it to the two roles that run a project.

The string was already in use as a `require_permission` argument by seven Agent Studio
routes while being in NO catalogue and granted to NO role, so those routes answered only
to `admin:*` — silently, because a permission nobody holds refuses everybody without
erroring. Those routes have since moved to `skill:*`. The frontend meanwhile passed the
same string to `hasPermission()` for the project Settings tab and the budget field, where
the identical emptiness meant a Project Admin and a Business Unit Admin saw no Settings
tab at all.

It is now a real permission meaning "edit this project's settings and budget", enforced by
`PATCH /projects/{id}` — which previously demanded `workspace:manage`, held only by
bu_admin, so the Project Admin who is made to choose a budget at creation could not change
the figure afterwards.

Granted to:
    bu_admin       runs the unit and everything in it
    project_admin  runs the project, and is asked to set its budget

Archive and restore deliberately stay on `workspace:manage`: removing a project from a
unit is the unit's call, not the project's.

WHY A MIGRATION, same reasoning as 0018 and 0019. `role_permissions` is code-owned and
reconciled from `_ROLE_PERMISSIONS` on boot, but `assert_rbac_catalog` VERIFIES before it
seeds and raises `RbacCatalogDriftError` on any difference, self-seeding only when `roles`
is empty. A code-matrix change without its data migration therefore makes an existing
database refuse to start. Alembic runs before app start, so these rows exist by the time
the guard looks.

See finding "project:update is not a real permission" in docs/rbac-audit-2026-08-17.md.
"""
from alembic import op

revision = "0020_project_update_permission"
down_revision = "0019_governance_decide"
branch_labels = None
depends_on = None

_PERMISSION = "project:update"
_ROLES = ["bu_admin", "project_admin"]


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
