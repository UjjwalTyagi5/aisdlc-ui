"""Give three pipeline gates an approver who is not the Organization Admin.

`_PHASE_PERMISSION` maps all eight stages to an `artifact:approve_<phase>` permission,
and `runs.py::copilot_advance` enforces it with `has_permission` — an EXACT membership
test, so the generic `approve` does not imply the specific one. Three of those
permissions were granted to no role at all:

    code_review    -> artifact:approve_code_review
    security       -> artifact:approve_security
    documentation  -> artifact:approve_documentation

which left those gates passable only through the Organization Admin's `admin:*`
wildcard. The Security Engineer held `approve` but not `artifact:approve_security`,
so the role that OWNS the Security gate (frontend/lib/roles.ts::AGENT_OWNER_ROLE)
could not sign it off.

WHY A MIGRATION AND NOT JUST THE CODE MATRIX. `role_permissions` is code-owned and
reconciled from `_ROLE_PERMISSIONS` on boot — but `assert_rbac_catalog` runs
`verify_rbac_catalog` BEFORE any seeding and raises `RbacCatalogDriftError` on any
difference in either direction, only self-seeding when the `roles` table is entirely
empty. So changing the code matrix alone makes an existing database refuse to start:
"3 RBAC catalogue difference(s) … Refusing to start" (reproduced locally before this
migration was written). Alembic runs before app start, so these edges exist by the
time the guard looks.

THE EDGES ARE WRITTEN AS LITERALS, not imported from `_ROLE_PERMISSIONS`. A migration
is a fixed historical fact; importing a constant that keeps moving would make replaying
this revision at a later HEAD produce a different result. (0001_baseline legitimately
imports the matrix because it IS "whatever the code says" — an incremental migration is
not.)

Idempotent: ON CONFLICT DO NOTHING, so it is a no-op on a database that was baselined
after the code change, or where an operator already ran with RBAC_CATALOG_AUTOREPAIR.

Revision ID: 0018_grant_phase_approvals
Revises: 0017_model_gateway_cascade
"""
from alembic import op

revision = "0018_grant_phase_approvals"
down_revision = "0017_model_gateway_cascade"
branch_labels = None
depends_on = None

# (role, permission) — mirrors AGENT_OWNER_ROLE for these three stages.
_EDGES = [
    ("architect", "artifact:approve_code_review"),
    ("security_engineer", "artifact:approve_security"),
    ("project_admin", "artifact:approve_documentation"),
]


def upgrade() -> None:
    for role, permission in _EDGES:
        # The permission rows themselves already exist — all eight phase permissions
        # have been in `_PERMISSION_CATALOG` (and so in `permissions`) since the
        # baseline; only the role edges were missing. Inserted defensively anyway so
        # the FK cannot fail on a database seeded from an older catalogue.
        op.execute(
            "INSERT INTO permissions (name) VALUES ('%s') ON CONFLICT (name) DO NOTHING"
            % permission
        )
        op.execute(
            "INSERT INTO role_permissions (role_name, permission_name) "
            "VALUES ('%s', '%s') ON CONFLICT (role_name, permission_name) DO NOTHING"
            % (role, permission)
        )


def downgrade() -> None:
    # Only the edges. The permissions stay: they are referenced by _PHASE_PERMISSION
    # and dropping them would break the gate lookup rather than merely un-grant it.
    for role, permission in _EDGES:
        op.execute(
            "DELETE FROM role_permissions WHERE role_name = '%s' AND permission_name = '%s'"
            % (role, permission)
        )
