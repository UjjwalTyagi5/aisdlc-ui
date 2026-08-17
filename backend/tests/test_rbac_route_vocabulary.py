"""Every permission a route demands must be a real, grantable one.

The D-05 boot scan proves each route made an authz DECISION. It does not check that
the decision is expressible: `require_permission("project:update")` passes the scan
happily, and `project:update` was not in the catalogue and not granted to any role, so
seven Agent Studio routes were reachable only by an Organization Admin's `admin:*`
wildcard. Nothing failed — the endpoints simply refused everybody else, quietly, and
`skill:edit` (granted to Developer for exactly this) was enforced nowhere.

A typo'd or invented permission string is indistinguishable from a deliberate one at
the call site. This is the check that tells them apart.
"""
import inspect

import pytest
from fastapi.routing import APIRoute

import process_api
from shared.authz.permissions import ALL_PERMISSIONS, _ROLE_PERMISSIONS

WILDCARDS = {"admin:*", "platform:*"}


def _perms_of(call) -> set[str]:
    """require_permission captures `perm`; require_any_permission captures `perms`."""
    try:
        nl = inspect.getclosurevars(call).nonlocals
    except (TypeError, ValueError):  # pragma: no cover - non-closure dependency
        return set()
    out: set[str] = set()
    one = nl.get("perm")
    if isinstance(one, str):
        out.add(one)
    many = nl.get("perms")
    if isinstance(many, (tuple, list, set)):
        out.update(p for p in many if isinstance(p, str))
    return out


def _route_requirements() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for route in process_api.app.routes:
        if not isinstance(route, APIRoute):
            continue
        perms: set[str] = set()
        for dep in route.dependant.dependencies:
            call = getattr(dep, "call", None)
            if getattr(call, "__rbac_require_permission__", False):
                perms |= _perms_of(call)
        if perms:
            methods = ",".join(sorted(route.methods - {"HEAD", "OPTIONS"}))
            out[f"{methods} {route.path}"] = perms
    return out


REQUIREMENTS = _route_requirements()


def test_some_routes_were_actually_inspected():
    """Guard the guard: a broken extractor would make every assertion below vacuous."""
    assert len(REQUIREMENTS) > 100, len(REQUIREMENTS)


@pytest.mark.parametrize("route,perms", sorted(REQUIREMENTS.items()))
def test_route_demands_only_catalogued_permissions(route, perms):
    unknown = {p for p in perms if p not in ALL_PERMISSIONS and p not in WILDCARDS}
    assert not unknown, (
        f"{route} requires {sorted(unknown)}, which is not in the permission catalogue. "
        "Either add it to _PERMISSION_CATALOG (and to the frontend's "
        "permission-catalog.ts, which is the spec) or use an existing permission — "
        "an uncatalogued string can never be granted, so the route is reachable only "
        "via the admin:* wildcard."
    )


# Which role OWNS each gate, mirrored from frontend/lib/roles.ts::AGENT_OWNER_ROLE
# (the frontend is the spec for this vocabulary — see permissions.py's mirror contract).
# Keys are BACKEND stage names; the frontend calls `code_review` simply `review`.
PHASE_OWNER = {
    "requirements": "ba",
    "design": "architect",
    "development": "architect",
    "code_review": "architect",
    "security": "security_engineer",
    "testing": "qa",
    "deployment": "devops_engineer",
    "documentation": "project_admin",
}


def test_every_gate_can_be_passed_by_the_role_that_owns_it():
    """The invariant the route scan above structurally cannot check.

    Phase gates are enforced IN-BODY — `has_permission(perms, _PHASE_PERMISSION[stage])`
    inside `runs.py::copilot_advance` — not by a route dependency, so they carry no
    `__rbac_require_permission__` sentinel and `_route_requirements()` never sees them.
    That blind spot is precisely how three of the eight gates came to be passable only
    by an `admin:*` holder: `artifact:approve_code_review`, `_security` and
    `_documentation` were granted to no role at all, so the Security Engineer could not
    sign off the Security stage they own.

    Stated in the product's own terms — "the role that owns a gate can pass it" —
    rather than in permission strings, because that is the thing that must stay true.
    """
    from shared.authz.permissions import _PHASE_PERMISSION

    broken = []
    for stage, owner in PHASE_OWNER.items():
        required = _PHASE_PERMISSION.get(stage)
        assert required, f"stage {stage!r} has no entry in _PHASE_PERMISSION"
        held = _ROLE_PERMISSIONS.get(owner, [])
        if required not in held and not (set(held) & WILDCARDS):
            broken.append(f"{stage}: {owner} does not hold {required}")

    assert not broken, (
        "gates whose owning role cannot pass them: " + "; ".join(broken)
    )


def test_every_phase_permission_is_held_by_a_non_wildcard_role():
    """A gate only `admin:*` can pass is a gate with nobody behind it."""
    from shared.authz.permissions import _PHASE_PERMISSION

    granted = set()
    for grants in _ROLE_PERMISSIONS.values():
        granted.update(g for g in grants if g not in WILDCARDS)

    orphaned = sorted(set(_PHASE_PERMISSION.values()) - granted)
    assert not orphaned, (
        f"phase permissions granted to no role: {orphaned}. "
        "Only an Organization Admin could approve those stages."
    )


# Permissions that are DELIBERATELY reachable only through the `admin:*` wildcard.
# `_PERMISSION_CATALOG`'s own comment names this class: "Some entries (role:manage,
# settings:manage) are operationally reachable only via the admin:* wildcard, so they
# never appear as literal strings in any role grant."
#
# The entry must be earned. An empty exception list is the goal; anything added here is
# a claim that no role short of an Organization Admin should ever hold it, which is a
# product decision, not a convenience for making a test pass.
# Empty, and worth keeping that way. `settings:manage` was the obvious candidate and
# turned out not to need one: `read_scope.is_org_wide()` already tests for it in-body on
# the routes that matter, so it is enforced without being a route dependency.
ADMIN_ONLY_BY_DESIGN: set[str] = set()


def test_every_enforced_permission_is_held_by_some_role():
    """A permission no role holds gates an endpoint only org_admin can reach.

    Reported as one assertion rather than per-route so the failure names the whole set:
    these tend to arrive together, from one router written against a vocabulary that
    was never added to the matrix.
    """
    enforced: set[str] = set()
    for perms in REQUIREMENTS.values():
        enforced |= perms

    granted: set[str] = set()
    for grants in _ROLE_PERMISSIONS.values():
        granted.update(g for g in grants if g not in WILDCARDS)

    orphaned = sorted(
        p for p in enforced
        if p not in granted and p not in WILDCARDS and p not in ADMIN_ONLY_BY_DESIGN
    )
    assert not orphaned, (
        f"enforced by a route but granted to no role: {orphaned}. "
        "Only an admin:* holder can reach those endpoints. Either grant it to the role "
        "that should have it, or add it to ADMIN_ONLY_BY_DESIGN with the reason."
    )


def test_admin_only_exceptions_are_still_ungranted():
    """The exception list must not outlive its reason.

    If a permission listed there is later granted to a role, the entry is stale and the
    blanket exception would start hiding a real orphan behind it.
    """
    granted = set()
    for grants in _ROLE_PERMISSIONS.values():
        granted.update(g for g in grants if g not in WILDCARDS)

    stale = sorted(ADMIN_ONLY_BY_DESIGN & granted)
    assert not stale, (
        f"listed as admin-only but now granted to a role: {stale}. "
        "Remove them from ADMIN_ONLY_BY_DESIGN."
    )
