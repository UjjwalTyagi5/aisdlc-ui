"""Add `artifact:approve_plan` and grant it to the two roles that own the PM agent.

A plan commits people and dates, so accepting one is a decision somebody signs rather
than an output that simply appears — hence a phase gate, like every other stage.

Granted to:
    scrum_master   AGENT_OWNER_ROLE.plan. The PM agent is the first thing this role
                   OWNS rather than merely uses; it is the role whose job this is.
    project_admin  "owner" on every agent by design, and the fallback approver.

NOT granted to architect or ba. They hold their own phase gates and `use` reach on the
planner; owning the schedule is not the same as being able to talk to the agent.

WHY A MIGRATION, same reasoning as 0018-0020 and 0039. `role_permissions` is code-owned
and reconciled from `_ROLE_PERMISSIONS` on boot, but `assert_rbac_catalog` VERIFIES
before it seeds and raises `RbacCatalogDriftError` on any difference, self-seeding only
when `roles` is empty. A code-matrix change without its data migration therefore makes
an EXISTING database refuse to start. Alembic runs before app start, so these rows exist
by the time the guard looks.
"""
from alembic import op

revision = "0042_artifact_approve_plan"
down_revision = "0041_plan_artifacts"
branch_labels = None
depends_on = None

_PERMISSION = "artifact:approve_plan"
_ROLES = ["project_admin", "scrum_master"]


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
