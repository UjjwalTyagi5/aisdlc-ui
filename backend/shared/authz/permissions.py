"""Canonical RBAC permission matrix and phase-to-permission map.

Single source of truth imported by both the FastAPI require_permission dependency
and the Temporal signal handler — no dual maintenance (D-01).

No FastAPI, no SQLAlchemy, no DB session imports here — intentionally import-cheap
so the Temporal activity path can import this without pulling web framework deps.
"""

# Role -> permission strings (D-07: tech_lead includes approve_development to close the
# pipeline gap; sre_lead gets approve_deployment for forward-compat with the future
# deployment phase even though it does not exist in the current 4-phase MVP).
_ROLE_PERMISSIONS: dict[str, list[str]] = {
    # Admin tier — both keys grant the org-wide wildcard. `org_admin` is the
    # spec/display name; `admin` is the pre-existing seeded key (kept for back-compat).
    "admin": ["admin:*"],
    "org_admin": ["admin:*"],
    "delivery_lead": [
        "run:create", "run:view", "run:cancel",
        "artifact:view", "artifact:export",
        "artifact:approve_security",
        "artifact:approve_documentation",
        "workspace:manage", "member:manage",
        "connector:view", "cost:view", "eval:view",
    ],
    "product_manager": [
        "run:create", "run:view",
        "artifact:view", "artifact:export",
        "artifact:approve_requirements",
        "connector:view",
    ],
    # Display label "Architect" (see admin._ROLE_META); key kept as tech_lead.
    "tech_lead": [
        "run:create", "run:view",
        "artifact:view", "artifact:export",
        "artifact:approve_design",
        "artifact:approve_development",
        "artifact:approve_code_review",
        "connector:view",
    ],
    "developer": [
        "run:create", "run:view",
        "artifact:view", "artifact:export",
        "connector:view",
    ],
    "qa_lead": [
        "run:view",
        "artifact:view", "artifact:export",
        "artifact:approve_testing",
        "connector:view",
    ],
    # Display label "Release Manager"; key kept as sre_lead.
    "sre_lead": [
        "run:view",
        "artifact:view", "artifact:export",
        "artifact:approve_deployment",
        "connector:view",
    ],
    "security_auditor": [
        "run:view", "artifact:view", "artifact:export",
        "audit:view", "cost:view", "eval:view",
        "connector:view",
    ],
    "stakeholder": ["artifact:view"],
}

# Pipeline phase -> required permission string (D-01, D-07).
# All eight pipeline stages covered so no stage maps to an ungranted permission (Pitfall 6).
# Owners per blueprint §4.3: code_review->architect(tech_lead), security->delivery_lead,
# deployment->release_manager(sre_lead), documentation->delivery_lead (auto-approved by
# policy — see progression/advance rule — but still holds a real approve permission for
# the manual-override path).
_PHASE_PERMISSION: dict[str, str] = {
    "requirements": "artifact:approve_requirements",
    "design": "artifact:approve_design",
    "development": "artifact:approve_development",
    "code_review": "artifact:approve_code_review",
    "security": "artifact:approve_security",
    "testing": "artifact:approve_testing",
    "deployment": "artifact:approve_deployment",
    "documentation": "artifact:approve_documentation",
}

# The permission catalog is the universe of valid leaf permission strings.
# Role grants are a SUBSET of this catalog. Some entries (e.g. role:manage,
# settings:manage, connector:manage) are operationally reachable only via the
# admin:* wildcard, so they never appear as literal strings in any role grant —
# but they are still valid permissions and must be enumerable. The wildcards
# admin:*/platform:* are NOT catalog entries (they are not grantable leaves).
_PERMISSION_CATALOG: list[str] = [
    "run:create", "run:view", "run:cancel",
    "artifact:view", "artifact:export",
    "artifact:approve_requirements",
    "artifact:approve_design",
    "artifact:approve_development",
    "artifact:approve_code_review",
    "artifact:approve_security",
    "artifact:approve_testing",
    "artifact:approve_deployment",
    "artifact:approve_documentation",
    "connector:view", "connector:manage",
    "workspace:manage", "member:manage", "role:manage",
    "audit:view", "cost:view", "trace:view", "eval:view",
    "settings:manage",
    "model:manage",
]

# Derived exports — computed once at import time.
ALL_ROLES: list[str] = sorted(_ROLE_PERMISSIONS)
ALL_PERMISSIONS: list[str] = sorted(set(_PERMISSION_CATALOG))


def has_permission(perms: list[str], required: str) -> bool:
    """Return True if perms grants the required permission.

    admin:* is a wildcard that passes every permission check — keeps enforcement
    sites identical at both the FastAPI dependency and the Temporal signal handler.
    """
    return required in perms or "admin:*" in perms
