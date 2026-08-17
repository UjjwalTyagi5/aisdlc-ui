"""Canonical RBAC permission matrix and phase-to-permission map.

Single source of truth imported by both the FastAPI require_permission dependency
and every non-HTTP caller — no dual maintenance (D-01).

No FastAPI, no SQLAlchemy, no DB session imports here — intentionally import-cheap
so a worker or agent process can import this without pulling web framework deps.

MIRROR CONTRACT — this file is the server half of a pair. Its counterparts are:
    frontend/lib/roles.ts                  — PlatformRole, tier, scope
    frontend/lib/auth/role-permissions.ts  — ROLE_PERMISSIONS
    frontend/lib/auth/permission-catalog.ts — the grantable permission catalog
The frontend is the spec: it was designed first and the UI gates off these exact
strings. When a role or permission changes, change it there and mirror it here.
test_enterprise_rbac_catalog.py asserts the seeded DB matches this module, so drift
between code and database is caught; drift against the frontend is caught by review.
"""

# Which tier a role belongs to. Governance approves work, delivery does the work.
# A person must never hold both tiers within ONE scope (that is self-approval), but
# holding governance in one scope and delivery in another is legitimate and common.
# The per-scope check lives in shared/authz/grant.py::_assert_no_tier_conflict —
# it cannot be a table constraint because the rule is scoped, not global.
ROLE_TIER: dict[str, str] = {
    "org_admin": "governance",
    "bu_admin": "governance",
    "contributor": "delivery",
    "project_admin": "delivery",
    "ba": "delivery",
    "architect": "delivery",
    "developer": "delivery",
    "qa": "delivery",
    "security_engineer": "delivery",
    "devops_engineer": "delivery",
    "data_engineer": "delivery",
    "scrum_master": "delivery",
    "custom": "delivery",
}

# The level a role is normally granted at. Advisory — grant.py accepts any scope_kind
# so a deliberate exception (say, an architect bound at business-unit level) stays
# possible; this records the default the UI offers.
ROLE_SCOPE: dict[str, str] = {
    "org_admin": "organization",
    "bu_admin": "business_unit",
    "contributor": "business_unit",
    "project_admin": "project",
    "ba": "project",
    "architect": "project",
    "developer": "project",
    "qa": "project",
    "security_engineer": "project",
    "devops_engineer": "project",
    "data_engineer": "project",
    "scrum_master": "project",
    "custom": "configurable",
}

# Role -> permission strings. Mirrors ROLE_PERMISSIONS in the frontend exactly.
_ROLE_PERMISSIONS: dict[str, list[str]] = {
    # Org Admin holds the org-wide wildcard. Note it deliberately does NOT create
    # projects — that is a bu_admin/project_admin act. The wildcard would technically
    # permit it, so the UI gates project creation on role, not permission.
    "org_admin": ["admin:*"],
    "bu_admin": [
        # The read-only floor, and it was MISSING here while `contributor` — a
        # strictly weaker role — had it. Every route under the app's artifact:view
        # dependency (workspaces, projects, runs, audit, cost, traces) therefore
        # 403'd a Business Unit Admin: they could administer a unit they could not
        # open. Governance roles do not RUN agents (PRD §14.8), which is why the
        # approve/invoke permissions are absent below — but not being able to read
        # was never the intent, only a consequence of reading the floor as a
        # delivery permission.
        "artifact:view",
        "member:manage", "role:manage",
        # Both halves of connector, explicitly. has_permission is an exact membership
        # test with no manage->view implication, so "connector:manage" alone left this
        # role failing the Integrations page's "connector:view" gate.
        "connector:manage", "connector:view",
        "project:create", "model:manage",
        "audit:view", "cost:view",
        "workspace:manage",
        # Tier 2 of routing.REQUEST_ESCALATION_CHAIN.
        "governance:decide",
    ],
    # Onboarded into a unit and holding nothing until that unit's admin assigns a real
    # role. artifact:view is the read-only floor — enough to sign in and see the shell
    # rather than an error page, deliberately not enough to open an agent or raise a run.
    "contributor": ["artifact:view"],
    "project_admin": [
        "member:manage", "project:create", "model:manage",
        "run:create", "run:view", "run:cancel",
        "artifact:view", "artifact:export",
        "agent:invoke", "approve",
        # Documentation's owner (AGENT_OWNER_ROLE.documentation) is project_admin, the
        # fallback approver — its acceptance is automatic and no delivery role owns it.
        "artifact:approve_documentation",
        "connector:view", "connector:manage",
        "cost:view", "trace:view",
        # Tier 1 of routing.REQUEST_ESCALATION_CHAIN — the first approver a request
        # reaches, and the reason this is not a governance-tier-only permission.
        "governance:decide",
    ],
    "ba": [
        "run:create", "run:view",
        "artifact:view", "artifact:export",
        "agent:invoke", "approve",
        "artifact:approve_requirements",
        "connector:view",
    ],
    "architect": [
        "run:create", "run:view",
        "artifact:view", "artifact:export",
        "agent:invoke", "approve",
        "artifact:approve_design",
        "artifact:approve_development",
        # AGENT_OWNER_ROLE.review is architect, so the Code Review gate routes here —
        # and _PHASE_PERMISSION demands this exact string. Missing, the gate was
        # passable only via the admin:* wildcard.
        "artifact:approve_code_review",
        "connector:view",
    ],
    "developer": [
        "run:create", "run:view",
        "artifact:view", "artifact:export",
        "agent:invoke",
        "connector:view",
        "skill:edit",
    ],
    "qa": [
        "run:view",
        "artifact:view", "artifact:export",
        "agent:invoke", "approve",
        "artifact:approve_testing",
        "connector:view",
    ],
    "security_engineer": [
        "run:view",
        "artifact:view", "artifact:export",
        "agent:invoke", "approve",
        # Its own gate. has_permission is an exact membership test — the generic
        # "approve" above does NOT imply this, which is why the Security Engineer
        # could not sign off the Security stage.
        "artifact:approve_security",
        "connector:view",
        "audit:view", "cost:view", "trace:view",
    ],
    "devops_engineer": [
        "run:view",
        "artifact:view", "artifact:export",
        "agent:invoke", "approve",
        "artifact:approve_deployment",
        "connector:view",
    ],
    "data_engineer": [
        "run:create", "run:view",
        "artifact:view", "artifact:export",
        "agent:invoke", "approve",
        "connector:view",
    ],
    "scrum_master": [
        "run:view",
        "artifact:view", "artifact:export",
        "agent:invoke",
        "connector:view",
    ],
    # Placeholder for tenant-defined roles. The real grants live in
    # custom_role_permissions; this floor applies before any are attached.
    "custom": ["artifact:view"],
}

# Pipeline phase -> required permission string (D-01, D-07).
# All eight stages are covered so no stage maps to an ungranted permission (Pitfall 6).
# Owners follow AGENT_OWNERSHIP in frontend/lib/roles.ts:
#   requirements->ba, design/development/code_review->architect, testing->qa,
#   security->security_engineer, deployment->devops_engineer.
# documentation has no dedicated approver role, so it stays with project_admin, which
# holds the generic "approve" and is FALLBACK_APPROVER on the frontend.
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
# Role grants are a SUBSET of this catalog. Some entries (role:manage, settings:manage)
# are operationally reachable only via the admin:* wildcard, so they never appear as
# literal strings in any role grant — but they are still valid and must be enumerable.
# The wildcards admin:*/platform:* are NOT catalog entries (not grantable leaves).
#
# Mirrors ALL_GRANTABLE_PERMISSIONS in frontend/lib/auth/permission-catalog.ts, plus
# the artifact:approve_* phase permissions that _PHASE_PERMISSION depends on —
# shared/routers/signals.py resolves the required permission from a run's current
# phase at request time, so every phase permission must exist even where no role
# currently lists it (code_review, security and documentation are approved via the
# generic "approve" held by project_admin).
_PERMISSION_CATALOG: list[str] = [
    # Access management
    "member:manage", "role:manage",
    # Runs
    "run:create", "run:view", "run:cancel",
    # Artifacts
    "artifact:view", "artifact:export",
    "approve",
    "artifact:approve_requirements",
    "artifact:approve_design",
    "artifact:approve_development",
    "artifact:approve_code_review",
    "artifact:approve_security",
    "artifact:approve_testing",
    "artifact:approve_deployment",
    "artifact:approve_documentation",
    # Agents
    "agent:invoke",
    # Connectors
    "connector:view", "connector:request", "connector:manage",
    # Governance
    # Deciding a governance request. NOT the same as being the person it is currently
    # waiting on: this says the role takes governance decisions at all, and
    # `routing.REQUEST_ESCALATION_CHAIN` still says whose turn it is. Without it the
    # lane was authorised purely by role-string matching, so a custom role could be
    # neither granted nor denied governance decisioning — the one thing custom roles
    # exist to express.
    "governance:decide",
    # Configuration
    "project:create", "model:manage", "settings:manage", "workspace:manage",
    # Observability
    "audit:view", "cost:view", "trace:view", "eval:view",
    # Agent Studio
    "skill:edit", "skill:edit:project", "skill:promote", "skill:approve", "skill:import",
]

# Derived exports — computed once at import time.
ALL_ROLES: list[str] = sorted(_ROLE_PERMISSIONS)
ALL_PERMISSIONS: list[str] = sorted(set(_PERMISSION_CATALOG))


def has_permission(perms: list[str], required: str) -> bool:
    """Return True if perms grants the required permission.

    admin:* is a wildcard that passes every permission check — keeps enforcement
    sites identical at the FastAPI dependency and at every non-HTTP caller.

    Exact membership, no implication: holding "connector:manage" does NOT satisfy
    "connector:view". Roles must list both halves. hasPermission in
    frontend/lib/auth/permissions.ts mirrors this test exactly.
    """
    return required in perms or "admin:*" in perms


def tiers_conflict(a: str | None, b: str | None) -> bool:
    """True if two tiers may not coexist within a single scope.

    Mirrors tiersConflict in frontend/lib/roles.ts.
    """
    if a is None or b is None:
        return False
    return a != b
