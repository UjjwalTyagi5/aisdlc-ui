"""Phase 1 — RBAC catalog expansion: roles, permissions, connector split."""
from shared.authz.dependency import _low_cardinality_role
from shared.authz.permissions import (
    ALL_PERMISSIONS,
    ALL_ROLES,
    _ROLE_PERMISSIONS,
    has_permission,
)

EXPECTED_ROLES = {
    "org_admin", "admin", "delivery_lead", "product_manager", "tech_lead",
    "developer", "qa_lead", "sre_lead", "security_auditor", "stakeholder",
}

NEW_PERMISSIONS = {
    "run:view", "run:cancel", "artifact:export", "connector:view",
    "workspace:manage", "member:manage", "role:manage",
    "audit:view", "cost:view", "eval:view", "settings:manage",
}


def test_all_expected_roles_present():
    assert EXPECTED_ROLES.issubset(set(ALL_ROLES))


def test_all_new_roles_present():
    for role in ("delivery_lead", "security_auditor", "stakeholder"):
        assert role in ALL_ROLES, f"{role} missing from ALL_ROLES"


def test_all_new_permissions_present():
    assert NEW_PERMISSIONS.issubset(set(ALL_PERMISSIONS))


def test_security_auditor_is_read_only():
    perms = _ROLE_PERMISSIONS["security_auditor"]
    assert "audit:view" in perms and "cost:view" in perms and "eval:view" in perms
    assert not any(p.startswith("artifact:approve_") for p in perms)
    assert "run:create" not in perms


def test_stakeholder_view_only():
    assert _ROLE_PERMISSIONS["stakeholder"] == ["artifact:view"]


def test_developer_cannot_approve():
    perms = _ROLE_PERMISSIONS["developer"]
    assert "run:create" in perms
    assert not any(p.startswith("artifact:approve_") for p in perms)


def test_connector_view_granted_broadly_manage_admin_only():
    for role in ("developer", "qa_lead", "delivery_lead"):
        assert "connector:view" in _ROLE_PERMISSIONS[role]
    for role, perms in _ROLE_PERMISSIONS.items():
        if role in ("admin", "org_admin"):
            continue
        assert "connector:manage" not in perms, f"{role} must not have connector:manage"


def test_admin_wildcard_still_passes_new_perms():
    assert has_permission(["admin:*"], "role:manage") is True
    assert has_permission(["admin:*"], "audit:view") is True


def test_role_hint_for_auditor_and_stakeholder():
    assert _low_cardinality_role(["audit:view", "cost:view"]) == "security_auditor"
    assert _low_cardinality_role(["artifact:view"]) == "stakeholder"
    assert _low_cardinality_role(["admin:*"]) == "admin"
    assert _low_cardinality_role(["workspace:manage"]) == "delivery_lead"


def test_every_role_has_display_metadata():
    from shared.routers.admin import _ROLE_META
    from shared.authz.permissions import ALL_ROLES as _ALL_ROLES
    for role in _ALL_ROLES:
        assert role in _ROLE_META, f"{role} missing a label/description in _ROLE_META"
    # Enterprise-friendly labels over the kept internal keys.
    assert _ROLE_META["tech_lead"][0] == "Architect"
    assert _ROLE_META["sre_lead"][0] == "Release Manager"


def test_get_connectors_requires_view_permission():
    from shared.routers import connectors as conn_mod

    def _has_require_perm(path, method):
        for r in conn_mod.connectors_resource_router.routes:
            if getattr(r, "path", None) == path and method in (getattr(r, "methods", None) or set()):
                return any(
                    getattr(getattr(d, "call", None), "__rbac_require_permission__", False)
                    for d in r.dependant.dependencies
                )
        return False

    assert _has_require_perm("/connectors", "GET")
    assert _has_require_perm("/connectors/{kind}", "GET")


def test_migration_0011_is_catalog_only_and_chained():
    # Module name has a leading digit (`0011_...`) which is not a valid Python
    # identifier, so importlib.util.find_spec on the dotted path is unreliable
    # (may raise instead of returning None). Locate the file directly instead.
    import os
    here = os.path.dirname(__file__)
    path = os.path.normpath(
        os.path.join(here, "..", "migrations", "versions", "0011_rbac_catalog_expansion.py")
    )
    assert os.path.exists(path), "migration 0011 module not found"
    text = open(path, encoding="utf-8").read()
    assert 'revision = "0011"' in text
    assert 'down_revision = "0010"' in text
    # Must NEVER write user_workspace_roles (FORCE RLS would block the migration role).
    assert "user_workspace_roles" not in text
    # Must be idempotent.
    assert "ON CONFLICT DO NOTHING" in text


def test_migration_matrix_matches_code_matrix():
    """0011's literal matrix must equal _ROLE_PERMISSIONS (D-01 no-drift)."""
    import importlib.util
    from pathlib import Path

    mig_path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0011_rbac_catalog_expansion.py"
    spec = importlib.util.spec_from_file_location("_mig_0011", mig_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for role, perms in _ROLE_PERMISSIONS.items():
        assert set(mod._ROLE_PERMS.get(role, [])) == set(perms), (
            f"matrix drift for {role}: migration vs permissions.py"
        )
    # And the reverse: migration has no roles absent from the code matrix.
    for role in mod._ROLE_PERMS:
        assert role in _ROLE_PERMISSIONS, f"migration has unknown role {role}"
