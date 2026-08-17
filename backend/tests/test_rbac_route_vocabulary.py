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

    orphaned = sorted(p for p in enforced if p not in granted and p not in WILDCARDS)
    assert not orphaned, (
        f"enforced by a route but granted to no role: {orphaned}. "
        "Only an admin:* holder can reach those endpoints."
    )
