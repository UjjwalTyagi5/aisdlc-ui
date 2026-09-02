"""Add `artifact:delete` and grant it to the nine delivery roles.

Artifacts were immutable-and-forever: `PATCH /artifacts/{id}` is an accepted-but-no-op
stub honouring the immutability decision, and there was no delete at all. That is right
for an approved design document and wrong for the rest of what the table accumulates —
a failed generation, a duplicate, and in particular an artifact whose row exists while
its bytes never reached blob storage (`blob_url IS NULL`), which the UI lists as a
downloadable document that cannot be downloaded. There was no way to clear any of it.

WHY NOT artifact:export. Exporting takes a COPY out of the platform; deleting destroys
the original. A role that may read a design is not thereby entitled to destroy it, so
this is a separate leaf rather than an implication of an existing one.

Granted to the nine roles holding `agent:invoke` — the delivery roles that PRODUCE
artifacts, on the principle that whoever can generate an artifact can remove one:

    project_admin, ba, architect, developer, qa,
    security_engineer, devops_engineer, data_engineer, scrum_master

NOT granted to bu_admin or contributor. bu_admin is a governance role and governance
roles do not perform delivery acts (PRD §14.8) — the same reasoning that keeps
`agent:invoke` off it. contributor is the read-only floor. org_admin reaches this
through `admin:*` and needs no explicit grant.

WHY A MIGRATION, same reasoning as 0018, 0019 and 0020. `role_permissions` is
code-owned and reconciled from `_ROLE_PERMISSIONS` on boot, but `assert_rbac_catalog`
VERIFIES before it seeds and raises `RbacCatalogDriftError` on any difference,
self-seeding only when `roles` is empty. A code-matrix change without its data
migration therefore makes an EXISTING database refuse to start. Alembic runs before app
start, so these rows exist by the time the guard looks.
"""
from alembic import op

revision = "0039_artifact_delete_permission"
down_revision = "0038_run_created_by"
branch_labels = None
depends_on = None

_PERMISSION = "artifact:delete"

# The roles holding agent:invoke — kept as a literal list rather than derived from
# _ROLE_PERMISSIONS at run time, because a migration must describe the change it made
# when it ran, not whatever the matrix says on some later boot.
_ROLES = [
    "project_admin",
    "ba",
    "architect",
    "developer",
    "qa",
    "security_engineer",
    "devops_engineer",
    "data_engineer",
    "scrum_master",
]


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
