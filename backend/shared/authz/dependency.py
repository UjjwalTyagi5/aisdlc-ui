"""require_permission FastAPI dependency factory and public() marker.

require_permission(perm, *, run_param=None):
  Returns a FastAPI dependency closure that enforces deny-by-default access control.
  Reads permissions from request.state.permissions (baked into the JWT at login, D-02).
  Derives workspace from the resource (run_param path param -> resolve_workspace_for_run)
  or falls back to resolve_default_workspace (D-06).  On denial, increments RBAC_DENIALS
  and raises HTTP 403 with a generic body (no permission-name leak, Anti-Pattern).

public():
  Returns a no-op dependency closure tagged __rbac_public__=True so plan 04's boot scan
  can detect routes that consciously opt out of permission enforcement (D-05).

Boot-scan sentinels:
  closure.__rbac_require_permission__ = True  — protected route marker
  closure.__rbac_public__ = True              — opt-out marker for D-05 scan
"""
from __future__ import annotations

import logging

from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.requests import HTTPConnection

from shared.authz.permissions import has_permission
from shared.authz.workspace import (
    active_workspace_for_request,
    resolve_workspace_for_run,
)
from shared.services.metrics import RBAC_DENIALS

logger = logging.getLogger(__name__)


def require_permission(perm: str, *, run_param: str | None = None):
    """Return a FastAPI dependency closure enforcing deny-by-default access control.

    Args:
        perm:      The required permission string (e.g. "artifact:approve_requirements").
        run_param: Optional path parameter name containing a run_id.  When set AND the
                   parameter is present in the request, workspace is derived via
                   resolve_workspace_for_run (resource-derived, D-06).  Otherwise falls
                   back to resolve_default_workspace for resource-less checks.

    The returned closure is workspace-parameterized so per-workspace DB resolution is
    additive when multi-workspace ships in 7.3+ (Planner Note, D-06).
    """

    async def _dep(conn: HTTPConnection) -> None:
        # WebSocket connections authenticate via the single-use ws-ticket redeemed
        # INSIDE the handler (config/auth/ws_ticket); the HTTP JWT middleware does
        # not run for them, so conn.state is unpopulated and a Request can't be
        # injected. Applying this HTTP permission dep to a WS route otherwise crashes
        # with "_dep() missing 1 required positional argument: 'request'" and closes
        # every agent chat socket. The ws-ticket is itself minted behind
        # require_permission("artifact:view"), so deferring WS authz to the handler
        # is safe. Skip the check for WS; HTTP behaviour below is unchanged.
        if conn.scope.get("type") == "websocket":
            return
        request = conn
        perms: list[str] = getattr(request.state, "permissions", []) or []
        tenant_id: str = getattr(request.state, "tenant_id", "") or ""

        # Derive + stash the active workspace. Resource-derived for run routes;
        # otherwise the X-Workspace-Id selector (falls back to the org's first
        # workspace). Multi-workspace is now supported — no 409.
        try:
            if run_param and run_param in request.path_params:
                run_id = request.path_params[run_param]
                request.state.workspace_id = await resolve_workspace_for_run(run_id, tenant_id)
            else:
                await active_workspace_for_request(request, tenant_id)
        except HTTPException:
            # 404/403/422 — resource absent or bad selector; fall through to the
            # permission check which 403s (avoid leaking resource existence).
            pass

        if has_permission(perms, perm):
            return  # Access granted.

        # Deny — increment metric and raise generic 403 (do NOT name the missing perm
        # in the response body; log server-side only, Anti-Pattern).
        role_label = _low_cardinality_role(perms)
        RBAC_DENIALS.labels(permission=perm, role=role_label).inc()
        logger.warning(
            "RBAC denial: user=%s tenant=%s required=%s role_label=%s",
            getattr(request.state, "user_id", "unknown"),
            tenant_id,
            perm,
            role_label,
        )
        raise HTTPException(status_code=403, detail="Forbidden")

    _dep.__rbac_require_permission__ = True  # boot-scan sentinel (D-05)
    return _dep


def require_any_permission(*perms: str, run_param: str | None = None):
    """Like require_permission, but grants access if the caller holds ANY of `perms`.

    For endpoints with two legitimate consumer groups gated by different permissions —
    e.g. GET /model/availability, read by a Business Unit Admin's own governance view
    (model:manage) and by the run-time model picker / create-project dialog (run:create).
    Gating on a single permission here locks out whichever group doesn't hold it.
    """
    if not perms:
        raise ValueError("require_any_permission needs at least one permission")

    async def _dep(conn: HTTPConnection) -> None:
        if conn.scope.get("type") == "websocket":
            return
        request = conn
        caller_perms: list[str] = getattr(request.state, "permissions", []) or []
        tenant_id: str = getattr(request.state, "tenant_id", "") or ""

        try:
            if run_param and run_param in request.path_params:
                run_id = request.path_params[run_param]
                request.state.workspace_id = await resolve_workspace_for_run(run_id, tenant_id)
            else:
                await active_workspace_for_request(request, tenant_id)
        except HTTPException:
            pass

        if any(has_permission(caller_perms, p) for p in perms):
            return

        role_label = _low_cardinality_role(caller_perms)
        required_label = "|".join(perms)
        RBAC_DENIALS.labels(permission=required_label, role=role_label).inc()
        logger.warning(
            "RBAC denial: user=%s tenant=%s required_any=%s role_label=%s",
            getattr(request.state, "user_id", "unknown"),
            tenant_id,
            required_label,
            role_label,
        )
        raise HTTPException(status_code=403, detail="Forbidden")

    _dep.__rbac_require_permission__ = True  # boot-scan sentinel (D-05)
    return _dep


def public():
    """Return a no-op FastAPI dependency for routes that consciously opt out (D-05).

    The returned closure carries __rbac_public__=True so plan 04's startup boot scan
    can confirm the route has made an explicit authz decision rather than being forgotten.
    """

    async def _public_dep() -> None:
        pass  # Intentional no-op — route is marked public by design.

    _public_dep.__rbac_public__ = True  # boot-scan sentinel (D-05)
    return _public_dep


def _low_cardinality_role(perms: list[str]) -> str:
    """Derive a bounded role label from the permission set for metric cardinality safety.

    Uses the first recognizable high-privilege permission as a proxy (Pitfall 7 / A2).
    The label is advisory (not used for authz) — bounded to ~7 values so RBAC_DENIALS
    cardinality stays well within Prometheus label limits.
    """
    if "admin:*" in perms:
        return "admin"
    # Ordered most-privileged-first; first match wins. Bounded set keeps
    # RBAC_DENIALS label cardinality safe (Pitfall 7 / A2). Advisory only.
    _PERM_ROLE_HINTS = {
        "workspace:manage": "delivery_lead",
        "audit:view": "security_auditor",
        "artifact:approve_requirements": "product_manager",
        "artifact:approve_design": "tech_lead",
        "artifact:approve_development": "tech_lead",
        "artifact:approve_testing": "qa_lead",
        "artifact:approve_deployment": "sre_lead",
        "run:create": "developer",
        "artifact:view": "stakeholder",
    }
    for perm, role in _PERM_ROLE_HINTS.items():
        if perm in perms:
            return role
    return "unknown"


# ── D-05: startup route-coverage boot scan (SC-04, fail-open structurally impossible) ──
#
# Every APIRoute the app serves must make a CONSCIOUS authz choice: either
# require_permission(...)-protected, public()-marked, or — for the signals route,
# whose required permission is phase-derived at request time and therefore cannot
# be a static factory parameter (Pattern 4) — covered by its own in-body
# _check_permission_for_phase check (T-7.2-14). _SIGNALS_IN_BODY_PROTECTED_PATHS
# documents that conscious exception so the scan does not flag it as an offender.
#
# Mounts (/generated, /static), inbound webhooks (/webhooks/*, signature IS the
# auth — T-m6-07-S), the future SCIM surface (/scim/*), and a small fixed set of
# framework/health/docs endpoints are allowlisted by path or prefix — mirroring
# the EXACT exemptions the JWT middleware itself already grants (process_api.py
# _EXEMPT_PATHS + the /webhooks//generated//static prefix checks), so the scan
# can never drift out of sync with what is actually unauthenticated.

_PUBLIC_PATHS: set[str] = {"/", "/health", "/docs", "/openapi.json", "/redoc", "/metrics"}

_PUBLIC_PREFIXES: tuple[str, ...] = ("/webhooks/", "/generated", "/static", "/scim/")

# WHY an allowlist (not a sentinel) for the signals route: the required permission
# is derived at runtime from the run's current pipeline stage (requirements/design/
# development/testing), so it cannot be expressed as a single require_permission(perm)
# factory call ahead of time. signals.py's send_signal performs the equivalent
# deny-by-default check in-body via _check_permission_for_phase, BEFORE any Temporal
# handle is obtained (REQ-M7-11) — functionally equivalent to the factory dependency,
# just parameterized at request time instead of route-registration time (Pattern 4).
_SIGNALS_IN_BODY_PROTECTED_PATHS: set[str] = {"/runs/{run_id}/signals/{name}"}


def _route_has_require_permission(route: APIRoute) -> bool:
    """Return True if any of the route's resolved dependencies carries the
    require_permission boot-scan sentinel (__rbac_require_permission__)."""
    for dep in route.dependant.dependencies:
        call = getattr(dep, "call", None)
        if getattr(call, "__rbac_require_permission__", False):
            return True
    return False


def _route_is_public(route: APIRoute) -> bool:
    """Return True if any of the route's resolved dependencies carries the
    public() boot-scan sentinel (__rbac_public__), OR the path is in the
    explicit _PUBLIC_PATHS set / _PUBLIC_PREFIXES allowlist (D-05)."""
    if route.path in _PUBLIC_PATHS or route.path.startswith(_PUBLIC_PREFIXES):
        return True
    for dep in route.dependant.dependencies:
        call = getattr(dep, "call", None)
        if getattr(call, "__rbac_public__", False):
            return True
    return False


def assert_all_routes_protected(app) -> None:
    """Raise RuntimeError if any APIRoute is neither require_permission-protected,
    public()/allowlist-marked, nor the signals route's documented in-body exception.

    D-05 / SC-04: makes "ship a route, forget the authz dependency" a BOOT FAILURE
    instead of a silent fail-open security hole. Call AFTER all app.include_router(...)
    registrations, in lifespan startup, beside the BYPASSRLS guard (T-7.2-15).
    """
    offenders: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue  # WebSocketRoute / Mount / static — not subject to this scan
        if route.path in _SIGNALS_IN_BODY_PROTECTED_PATHS:
            continue
        if _route_is_public(route):
            continue
        if _route_has_require_permission(route):
            continue
        offenders.append(f"{sorted(route.methods or [])} {route.path}")
    if offenders:
        raise RuntimeError(
            "D-05: the following routes are unprotected — neither require_permission(...) "
            "nor public()/allowlist-marked, and ship with NO conscious authz decision "
            "(fail-open is structurally impossible; mark each route explicitly): "
            + ", ".join(offenders)
        )
