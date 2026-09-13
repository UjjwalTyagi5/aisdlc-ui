"""RBAC catalog: roles, permissions, tiers, and the seeded-vs-code drift guard."""
import pytest

from shared.authz.dependency import _low_cardinality_role
from shared.authz.permissions import (
    ALL_PERMISSIONS,
    ALL_ROLES,
    _ROLE_PERMISSIONS,
    has_permission,
)

# The 13 roles of the redesigned model. Mirrors PlatformRole in frontend/lib/roles.ts —
# if these drift apart the UI gates off strings the backend never grants.
EXPECTED_ROLES = {
    "org_admin", "bu_admin", "contributor", "project_admin",
    "ba", "architect", "developer", "qa",
    "security_engineer", "devops_engineer", "data_engineer", "scrum_master",
    "custom",
}

# NO eval:view — removed from the catalogue. It was granted to no role and required by
# no route (the eval endpoint gates on artifact:view), so it rendered as a checkbox on
# Roles & Access that changed nothing when ticked.
NEW_PERMISSIONS = {
    "run:view", "run:cancel", "artifact:export", "connector:view",
    "workspace:manage", "member:manage", "role:manage",
    "audit:view", "cost:view", "settings:manage",
}


def test_all_expected_roles_present():
    assert EXPECTED_ROLES.issubset(set(ALL_ROLES))


def test_role_catalog_is_exactly_the_expected_set():
    """No extras either — a leftover legacy role would still be grantable."""
    assert set(ALL_ROLES) == EXPECTED_ROLES


def test_all_new_permissions_present():
    assert NEW_PERMISSIONS.issubset(set(ALL_PERMISSIONS))


def test_agent_access_stage_two_owner_roles_hold_governance_decide():
    """Task 8: `agent_access` stage two routes to whichever delivery role owns the
    phase (routing.AGENT_OWNER_ROLE), and `POST /governance-approvals/{id}/decide` is
    gated on `governance:decide`. Without this permission the six delivery owner
    roles could never pass that floor to decide their own agent's stage-two request —
    a flat 403 before decide()'s own role-match check (`decider_role !=
    request["currentApproverRole"]`) is ever reached, making the effect wired up in
    `shared/governance/effects.py::_apply_agent_access` unreachable for every phase
    but `documentation` (owned by project_admin, which already holds the permission).

    Safe to grant broadly, not a platform-wide widening: `agent_access` stage two is
    the ONLY place any of these six roles is ever `currentApproverRole` —
    `GOVERNANCE_APPROVER_ROLE`/`REQUEST_ESCALATION_CHAIN` are exhaustively
    {project_admin, bu_admin, org_admin} for every other request type — so decide()'s
    existing role-match check narrows the grant to exactly the requests actually
    routed to each role. Holding `governance:decide` does not let a delivery role
    decide anyone else's request.
    """
    from shared.governance import routing

    owner_roles = set(routing.AGENT_OWNER_ROLE.values()) - {"project_admin"}
    # scrum_master joined this set when routing.AGENT_OWNER_ROLE gained its missing
    # `plan` entry. It was absent from 0037 only because the map had no `plan` key and
    # `agent_owner_role()` answered a "project_admin" default for the miss, so Plan
    # looked like a project_admin-owned agent. Granted by 0044.
    assert owner_roles == {
        "ba", "architect", "qa", "devops_engineer", "security_engineer",
        "data_engineer", "scrum_master",
        # Joined when "One agent, one role" moved the Development gate here.
        "developer",
    }
    for role in owner_roles:
        assert "governance:decide" in _ROLE_PERMISSIONS[role], (
            f"{role} owns an agent_access stage-two phase but cannot pass the "
            "governance:decide permission floor to decide it"
        )


def test_eval_view_is_not_in_the_catalogue():
    """A grantable permission with no enforcement is worse than no permission.

    eval:view sat in the catalogue, granted to no role and checked by no route —
    shared/routers/eval.py gates GET /runs/{run_id}/eval on artifact:view — so the
    Roles & Access page offered a checkbox that changed nothing. See finding 8 in
    docs/rbac-audit-2026-08-17.md.

    If evaluation visibility earns its own gate later, the string and a
    require_permission call site belong in the same change.
    """
    assert "eval:view" not in ALL_PERMISSIONS
    for role, perms in _ROLE_PERMISSIONS.items():
        assert "eval:view" not in perms, f"{role} grants a permission nothing enforces"


def test_the_traces_screen_matches_the_prd_visibility_matrix():
    """PRD §35: Traces — Org Admin "All", BU Admin "Unit", Project Admin "own projects",
    builders none, with the Security Engineer as the one contributor exception (§15.9).

    bu_admin held audit:view and cost:view but NOT trace:view, so the governance role
    accountable for a unit was refused the screen showing what that unit's agents did —
    while holding the audit trail of the same runs. Scoping needed no work: a
    business_unit binding already resolves to that unit's projects through
    visible_project_ids; only the grant was missing.
    """
    holders = {r for r in ALL_ROLES if "trace:view" in _ROLE_PERMISSIONS[r]}
    # org_admin reaches it through the admin:* wildcard rather than a literal grant.
    assert "admin:*" in _ROLE_PERMISSIONS["org_admin"]
    assert holders == {"bu_admin", "project_admin", "security_engineer"}, (
        "trace:view holders drifted from the PRD §35 matrix. Traces carry prompt and "
        "output previews, so widening this set is a deliberate decision, not a fix."
    )


def test_security_engineer_is_oversight_not_author():
    perms = _ROLE_PERMISSIONS["security_engineer"]
    assert "audit:view" in perms and "cost:view" in perms and "trace:view" in perms
    # Owns EXACTLY one phase gate — its own. This was `not any(startswith(...))`,
    # which read as "oversight, not author" but actually asserted the bug: the role
    # that AGENT_OWNER_ROLE says owns the Security stage could not sign it off,
    # because has_permission is an exact test and the generic "approve" it holds
    # does not imply the specific one. An exact set is the right shape here — it
    # catches the missing grant AND an accidental extra one, which the negation
    # could not.
    assert {p for p in perms if p.startswith("artifact:approve_")} == {
        "artifact:approve_security"
    }
    # Still not an author: it reviews and signs off, it does not start work.
    assert "run:create" not in perms


def test_contributor_is_the_read_only_floor():
    """Onboarded into a unit, holding nothing until that unit's admin assigns a role."""
    assert _ROLE_PERMISSIONS["contributor"] == ["artifact:view"]
    assert _ROLE_PERMISSIONS["custom"] == ["artifact:view"]


def test_developer_approves_only_the_stage_it_owns():
    """SUPERSEDED RULE, NARROWED — not deleted.

    This asserted the developer held NO `artifact:approve_*` at all, encoding
    "Developer builds; Architect approves — never self-approval." That split ended
    with frontend/lib/roles.ts's "One agent, one role" change: the Architect no
    longer REACHES the Development agent, so the gate moved to `developer` rather
    than sitting with a role that cannot open it.

    What survives is the part that still bites. Self-approval prevention is now
    per-PERSON rather than per-ROLE (the individual who ran a consequential action
    cannot approve their own; it escalates), so the invariant worth pinning is that
    a developer can approve its OWN stage and no other — a developer signing off
    Security or Deployment would be a real widening.
    """
    perms = _ROLE_PERMISSIONS["developer"]
    assert "run:create" in perms
    approvals = {p for p in perms if p.startswith("artifact:approve_")}
    assert approvals == {"artifact:approve_development"}


def test_connector_view_granted_broadly_manage_restricted():
    for role in ("developer", "qa", "architect", "ba", "devops_engineer"):
        assert "connector:view" in _ROLE_PERMISSIONS[role]
    # connector:manage means onboarding a provider, which only the roles that own a
    # scope may do. bu_admin runs its unit's connections; project_admin its project's.
    allowed_to_manage = {"org_admin", "bu_admin", "project_admin"}
    for role, perms in _ROLE_PERMISSIONS.items():
        if role in allowed_to_manage:
            continue
        assert "connector:manage" not in perms, f"{role} must not have connector:manage"


def test_every_role_has_a_tier_and_scope():
    from shared.authz.permissions import ROLE_SCOPE, ROLE_TIER
    for role in ALL_ROLES:
        assert ROLE_TIER.get(role) in ("governance", "delivery"), f"{role} has no tier"
        assert ROLE_SCOPE.get(role), f"{role} has no default scope"
    # Only the two administrative roles govern; everyone else delivers.
    assert {r for r in ALL_ROLES if ROLE_TIER[r] == "governance"} == {"org_admin", "bu_admin"}


def test_phase_permissions_all_exist_in_the_catalog():
    """signals.py resolves a phase to a permission at request time — every one must exist."""
    from shared.authz.permissions import _PHASE_PERMISSION
    for phase, perm in _PHASE_PERMISSION.items():
        assert perm in ALL_PERMISSIONS, f"phase {phase} maps to uncatalogued {perm}"


def test_admin_wildcard_still_passes_new_perms():
    assert has_permission(["admin:*"], "role:manage") is True
    assert has_permission(["admin:*"], "audit:view") is True


def test_role_hint_labels_are_bounded_and_current():
    """Advisory metric labels only — never authz. Must name roles that still exist."""
    assert _low_cardinality_role(["admin:*"]) == "admin"
    assert _low_cardinality_role(["role:manage"]) == "bu_admin"
    assert _low_cardinality_role(["artifact:approve_testing"]) == "qa"
    assert _low_cardinality_role(["artifact:view"]) == "contributor"


def test_every_role_has_display_metadata():
    from shared.routers.admin import _ROLE_META
    from shared.authz.permissions import ALL_ROLES as _ALL_ROLES
    for role in _ALL_ROLES:
        assert role in _ROLE_META, f"{role} missing a label/description in _ROLE_META"
    # Labels mirror ROLE_META in frontend/lib/roles.ts.
    assert _ROLE_META["architect"][0] == "Architect"
    assert _ROLE_META["bu_admin"][0] == "Business Unit Admin"
    assert _ROLE_META["org_admin"][0] == "Organization Admin"


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


def test_baseline_seeds_catalog_from_the_code_matrix():
    """The baseline must import the matrix, not restate it.

    The old 0011 carried a literal copy of _ROLE_PERMISSIONS, so this test compared the
    two dicts to catch drift. The baseline imports the real module instead, which makes
    drift impossible by construction — so what is worth asserting now is that it still
    does that, and still refuses to write into an RLS table.
    """
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0001_baseline.py"
    assert path.exists(), "baseline migration not found"
    text = path.read_text(encoding="utf-8")

    assert "from shared.authz.permissions import" in text, (
        "baseline must seed from shared.authz.permissions, not a hardcoded copy"
    )
    assert "_ROLE_PERMISSIONS" in text and "ALL_PERMISSIONS" in text

    # FORCE RLS applies to the migration role too, so seeding a tenant-scoped table
    # here would fail the WITH CHECK policy. role_bindings must start empty.
    assert "INSERT INTO role_bindings" not in text
    assert "bulk_insert(role_bindings" not in text

    # Exactly ONE baseline: the 39 migrations that preceded it were squashed into it,
    # and reintroducing a second baseline would give the chain two roots.
    #
    # Was `len(versions) == 1`, which also forbade any migration ever being added
    # after it — an assertion the squash never intended and which fires the first time
    # the schema legitimately changes. What matters is the single root, plus every
    # other revision chaining onto something.
    versions = sorted(p.name for p in path.parent.glob("[0-9]*.py"))
    baselines = [v for v in versions if "baseline" in v]
    assert baselines == ["0001_baseline.py"], f"expected one baseline, found {baselines}"

    for name in versions:
        if name in baselines:
            continue
        body = (path.parent / name).read_text(encoding="utf-8")
        assert "down_revision = " in body and "down_revision = None" not in body, (
            f"{name} must chain onto a previous revision, not start a second root"
        )


def test_seeded_catalog_matches_the_code_matrix():
    """The rows actually in the database must equal _ROLE_PERMISSIONS.

    This is the guarantee the old migration-vs-code comparison was reaching for, checked
    against the real seeded state rather than against source text. Skips without a DB.
    """
    import asyncio, os

    dsn = os.environ.get("POSTGRES_MIGRATIONS_CONN_STRING", "")
    if not dsn:
        pytest.skip("POSTGRES_MIGRATIONS_CONN_STRING not set")

    async def _check():
        import asyncpg
        conn = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            rows = await conn.fetch("SELECT role_name, permission_name FROM role_permissions")
            seeded: dict[str, set[str]] = {}
            for r in rows:
                seeded.setdefault(r["role_name"], set()).add(r["permission_name"])
            names = {r["name"] for r in await conn.fetch("SELECT name FROM roles")}
        finally:
            await conn.close()
        return seeded, names

    try:
        seeded, names = asyncio.run(_check())
    except Exception as exc:  # no live DB in this environment
        pytest.skip(f"database unavailable: {exc}")

    assert names == set(ALL_ROLES), "roles table does not match ALL_ROLES"
    for role, perms in _ROLE_PERMISSIONS.items():
        # Every granted string is seeded verbatim, wildcards included. ALL_PERMISSIONS is
        # the grantable-leaf catalog offered by the custom-role builder and excludes
        # admin:*, but role_permissions must still carry it — the resolver reads a user's
        # effective permissions straight out of that table, so filtering the wildcard out
        # would leave org_admin with nothing.
        assert seeded.get(role, set()) == set(perms), f"seeded grants drifted for {role}"
