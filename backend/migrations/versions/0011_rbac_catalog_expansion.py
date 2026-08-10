"""RBAC catalog expansion — new permissions + new roles + role_permission edges.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-11

GLOBAL catalog tables only (roles, permissions, role_permissions) — these are
non-RLS (D-03). Deliberately NEVER touches the FORCE-RLS workspace membership
table: that table is under FORCE RLS and the migration role cannot satisfy the
policy with the GUC unset (Pitfall 5 from 0007). Role keys are NOT renamed for
the same reason.

Idempotent via ON CONFLICT DO NOTHING — safe to re-run. Must stay in sync with
_ROLE_PERMISSIONS in shared/authz/permissions.py (D-01).
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_ROLE_PERMS: dict[str, list[str]] = {
    "admin": ["admin:*"],
    "org_admin": ["admin:*"],
    "delivery_lead": [
        "run:create", "run:view", "run:cancel", "artifact:view", "artifact:export",
        "artifact:approve_security", "artifact:approve_documentation",
        "workspace:manage", "member:manage", "connector:view", "cost:view", "eval:view",
    ],
    "product_manager": [
        "run:create", "run:view", "artifact:view", "artifact:export",
        "artifact:approve_requirements", "connector:view",
    ],
    "tech_lead": [
        "run:create", "run:view", "artifact:view", "artifact:export",
        "artifact:approve_design", "artifact:approve_development",
        "artifact:approve_code_review", "connector:view",
    ],
    "developer": [
        "run:create", "run:view", "artifact:view", "artifact:export", "connector:view",
    ],
    "qa_lead": [
        "run:view", "artifact:view", "artifact:export",
        "artifact:approve_testing", "connector:view",
    ],
    "sre_lead": [
        "run:view", "artifact:view", "artifact:export",
        "artifact:approve_deployment", "connector:view",
    ],
    "security_auditor": [
        "run:view", "artifact:view", "artifact:export",
        "audit:view", "cost:view", "eval:view", "connector:view",
    ],
    "stakeholder": ["artifact:view"],
}


def upgrade() -> None:
    for role, perms in _ROLE_PERMS.items():
        op.execute(
            f"INSERT INTO roles (name) VALUES ('{role}') ON CONFLICT (name) DO NOTHING"
        )
        for perm in perms:
            op.execute(
                f"INSERT INTO permissions (name) VALUES ('{perm}') ON CONFLICT (name) DO NOTHING"
            )
            op.execute(
                "INSERT INTO role_permissions (role_name, permission_name) "
                f"VALUES ('{role}', '{perm}') ON CONFLICT DO NOTHING"
            )
    print(
        "\n[0011] RBAC catalog expanded: org_admin/delivery_lead/security_auditor/"
        "stakeholder roles + new permission edges seeded (idempotent)."
    )


def downgrade() -> None:
    new_roles = ("org_admin", "delivery_lead", "security_auditor", "stakeholder")
    for role in new_roles:
        op.execute(f"DELETE FROM role_permissions WHERE role_name = '{role}'")
        op.execute(f"DELETE FROM roles WHERE name = '{role}'")
