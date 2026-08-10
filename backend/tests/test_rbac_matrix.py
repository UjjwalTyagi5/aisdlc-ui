"""Phase 6 — backend RBAC matrix gating tests.

Proves each matrix action requires its matrix permission (not a stricter/looser one)
and that delivery_lead — who has member:manage but NOT admin:* — can reach the
members admin surface. Uses a minimal app mounting the router with request.state
permissions injected (same idiom as test_platform_console.py router tests).
"""
import uuid

import email_validator as _ev
import pytest
from fastapi import FastAPI, Request
from httpx import AsyncClient, ASGITransport

# Allow .test TLD (RFC 6761 special-use) so pydantic EmailStr accepts test addresses.
_ev.SPECIAL_USE_DOMAIN_NAMES = [d for d in _ev.SPECIAL_USE_DOMAIN_NAMES if d != "test"]


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    """Dispose the shared async engine after each test to avoid event-loop reuse issues."""
    yield
    from shared.db import engine
    await engine.dispose()


def _app_with_perms(router, perms: list[str], tenant_id: str | None = None, prefix: str = ""):
    """Build a minimal FastAPI app that injects permissions + tenant_id into request.state.

    Pass `prefix` when the router registers routes as empty-path "" (e.g. cost_router
    registers GET "" → needs prefix="/cost"; projects_router needs prefix="/projects").
    FastAPI raises FastAPIError if both prefix and path are empty.
    """
    app = FastAPI()
    effective_tenant = tenant_id or "11111111-1111-1111-1111-111111111111"

    @app.middleware("http")
    async def _inject(request: Request, call_next):
        request.state.permissions = perms
        request.state.tenant_id = effective_tenant
        request.state.user_id = "tester"
        return await call_next(request)

    app.include_router(router, prefix=prefix)
    return app


# ---------------------------------------------------------------------------
# Task 1 — member:manage gate on admin_router
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_members_list_allowed_for_member_manage_without_admin_wildcard():
    """delivery_lead (member:manage only, NOT admin:*) must NOT be 403'd on GET /members.

    require_permission("member:manage"):
      - resolve_default_workspace raises 404 for an unknown tenant_id;
        the dependency catches that 404 and falls through to the permission check.
      - has_permission(["member:manage"], "member:manage") is True → 200 (empty list).
    Before the fix this returns 403 because _require_admin checks admin:*.
    """
    from shared.services.provisioning import provision_organization
    slug = f"rbac-mm-{uuid.uuid4().hex[:8]}"
    res = await provision_organization(
        "RBAC Test Org", slug, f"{slug}@t.test", "password123"
    )
    org_id = res["org_id"]
    workspace_id = str(uuid.uuid4())  # non-existent workspace → empty result, not error

    from shared.routers.admin import admin_router
    app = _app_with_perms(admin_router, ["member:manage"], tenant_id=org_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/members", params={"workspace_id": workspace_id})
    # 200 (empty list) — NOT 403. delivery_lead must reach this surface.
    assert r.status_code != 403, (
        f"Expected non-403 for member:manage caller, got {r.status_code}: {r.text}"
    )


@pytest.mark.asyncio
async def test_members_list_denied_without_member_manage():
    """A caller with only artifact:view (stakeholder-ish) must be 403'd on GET /members."""
    from shared.services.provisioning import provision_organization
    slug = f"rbac-av-{uuid.uuid4().hex[:8]}"
    res = await provision_organization(
        "RBAC Deny Org", slug, f"{slug}@t.test", "password123"
    )
    org_id = res["org_id"]
    workspace_id = str(uuid.uuid4())

    from shared.routers.admin import admin_router
    app = _app_with_perms(admin_router, ["artifact:view"], tenant_id=org_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/members", params={"workspace_id": workspace_id})
    assert r.status_code == 403, (
        f"Expected 403 for artifact:view-only caller, got {r.status_code}: {r.text}"
    )


# ---------------------------------------------------------------------------
# Task 2 — cost:view gate on cost_router + workspace:manage on projects mutations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cost_requires_cost_view_not_artifact_view():
    """artifact:view alone (developer) must NOT see cost — matrix says cost:view.

    The cost_router route is registered as path "" (root of the router); when
    mounted without a prefix in _app_with_perms it resolves to "/".
    Before the fix (artifact:view gate), this returns 200 — the test expects 403.
    """
    from shared.services.provisioning import provision_organization
    slug = f"rbac-cost-deny-{uuid.uuid4().hex[:8]}"
    res = await provision_organization(
        "Cost Deny Org", slug, f"{slug}@t.test", "password123"
    )
    org_id = res["org_id"]

    from shared.routers.cost import cost_router
    app = _app_with_perms(cost_router, ["artifact:view"], tenant_id=org_id, prefix="/cost")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/cost")  # cost_router registers GET "" → mounted at /cost
    assert r.status_code == 403, (
        f"Expected 403 for artifact:view-only caller on cost route, got {r.status_code}: {r.text}"
    )


@pytest.mark.asyncio
async def test_cost_allowed_with_cost_view():
    """cost:view permission must grant access to GET /cost (matrix: admin/delivery_lead/security_auditor).

    We mock the DB session so the aggregate query returns empty results — we are testing
    the authz gate only. A real org is provisioned so require_permission can resolve the
    default workspace without a 404 (which would fall through and still check perms).
    """
    from unittest.mock import AsyncMock, MagicMock

    from shared.services.provisioning import provision_organization
    slug = f"rbac-cost-allow-{uuid.uuid4().hex[:8]}"
    res = await provision_organization(
        "Cost Allow Org", slug, f"{slug}@t.test", "password123"
    )
    org_id = res["org_id"]

    from shared.routers.cost import cost_router
    from shared.db import get_db_session

    async def _mock_db():
        session = MagicMock()
        result = MagicMock()
        result.all = MagicMock(return_value=[])
        session.execute = AsyncMock(return_value=result)
        yield session

    app = _app_with_perms(cost_router, ["cost:view"], tenant_id=org_id, prefix="/cost")
    app.dependency_overrides[get_db_session] = _mock_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/cost")  # cost_router registers GET "" → mounted at /cost
    finally:
        app.dependency_overrides.clear()
    assert r.status_code != 403, (
        f"Expected non-403 for cost:view caller on cost route, got {r.status_code}: {r.text}"
    )


@pytest.mark.asyncio
async def test_project_create_denied_without_workspace_manage():
    """POST /projects must 403 callers without workspace:manage (developer has artifact:view but not workspace:manage)."""
    from shared.services.provisioning import provision_organization
    slug = f"rbac-proj-deny-{uuid.uuid4().hex[:8]}"
    res = await provision_organization(
        "Proj Deny Org", slug, f"{slug}@t.test", "password123"
    )
    org_id = res["org_id"]

    from shared.routers.projects import projects_router
    app = _app_with_perms(projects_router, ["artifact:view", "run:create"], tenant_id=org_id, prefix="/projects")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/projects", json={"name": "Blocked Project"})
    assert r.status_code == 403, (
        f"Expected 403 for artifact:view+run:create caller on POST /projects, got {r.status_code}: {r.text}"
    )


@pytest.mark.asyncio
async def test_project_create_allowed_with_workspace_manage():
    """POST /projects must NOT 403 callers with workspace:manage (admin/delivery_lead).

    We provision a real org so require_permission can resolve the default workspace.
    The DB session is mocked so the route body does not hit the real DB (avoids
    FK violations from the create_project nil-workspace placeholder). This test
    only asserts the RBAC gate (non-403), not the route's create logic.
    """
    from unittest.mock import AsyncMock, MagicMock

    from shared.services.provisioning import provision_organization
    slug = f"rbac-proj-allow-{uuid.uuid4().hex[:8]}"
    res = await provision_organization(
        "Proj Allow Org", slug, f"{slug}@t.test", "password123"
    )
    org_id = res["org_id"]

    # Build a minimal mock Project ORM object that ProjectOut.from_orm_project can consume.
    import uuid as _uuid
    from shared.models.orm import Project

    mock_proj = MagicMock(spec=Project)
    mock_proj.id = _uuid.uuid4()
    mock_proj.display_name = "New Project"
    mock_proj.tenant_id = _uuid.UUID(org_id)
    mock_proj.workspace_id = _uuid.UUID("00000000-0000-0000-0000-000000000001")
    mock_proj.archived = False
    mock_proj.description = None
    mock_proj.updated_at = None
    mock_proj.created_at = None

    async def _mock_db():
        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        # refresh sets the returned project attrs (called after flush)
        session.refresh = AsyncMock(side_effect=lambda p: None)
        # create_project resolves the tenant's workspace via db.execute(...) before
        # inserting the project; return a workspace id so it skips the create path.
        _ws_result = MagicMock()
        _ws_result.scalar_one_or_none = MagicMock(return_value=mock_proj.workspace_id)
        session.execute = AsyncMock(return_value=_ws_result)
        yield session

    from shared.routers.projects import projects_router
    from shared.db import get_db_session
    from shared.routers._schemas import ProjectOut

    app = _app_with_perms(projects_router, ["workspace:manage", "artifact:view"], tenant_id=org_id, prefix="/projects")
    app.dependency_overrides[get_db_session] = _mock_db

    original_from_orm = ProjectOut.from_orm_project
    try:
        # Patch from_orm_project to return a valid response from the mock ORM object.
        ProjectOut.from_orm_project = classmethod(lambda cls, p: ProjectOut(
            id=str(mock_proj.id),
            tenantId=org_id,
            name="New Project",
            slug="new-project",
            description=None,
            template="blank",
            archived=False,
            owners=[],
            pipeline=[],
            lastActivityAt="2026-01-01T00:00:00+00:00",
            createdAt="2026-01-01T00:00:00+00:00",
        ))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/projects", json={"name": "New Project"})
    finally:
        ProjectOut.from_orm_project = original_from_orm
        app.dependency_overrides.clear()

    assert r.status_code != 403, (
        f"Expected non-403 for workspace:manage caller on POST /projects, got {r.status_code}: {r.text}"
    )


# ---------------------------------------------------------------------------
# Task 3 — under-protection audit: no mutating org route is unprotected
# ---------------------------------------------------------------------------

def test_no_mutating_org_route_is_unprotected():
    """Every POST/PUT/PATCH/DELETE APIRoute in the app is either permission-gated
    (carries the __rbac_require_permission__ sentinel somewhere in its dependency
    chain) or is explicitly public()-marked / path-allowlisted.

    Mirrors the D-05 boot scan (assert_all_routes_protected) but filters to
    mutating methods and asserts a readable offender list so regressions fail loudly.
    The predicates used are the EXACT ones the shipped boot scan uses — no second
    definition.

    Note: the D-05 boot scan fires on import and covers ALL methods; this test is an
    additional regression guard scoped to mutating methods so CI surfaces the
    specific offending paths clearly.
    """
    import process_api  # importing runs the app build + existing D-05 boot scan
    from shared.authz.dependency import (
        _route_has_require_permission,
        _route_is_public,
        _SIGNALS_IN_BODY_PROTECTED_PATHS,
    )
    from fastapi.routing import APIRoute

    app = process_api.app
    offenders = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue  # WebSocketRoute / Mount / static — not subject to this scan
        methods = route.methods or set()
        if not (methods & {"POST", "PUT", "PATCH", "DELETE"}):
            continue
        if route.path in _SIGNALS_IN_BODY_PROTECTED_PATHS:
            continue
        if _route_is_public(route):
            continue
        if _route_has_require_permission(route):
            continue
        offenders.append((sorted(methods), route.path))

    assert offenders == [], (
        f"Unprotected mutating routes found — add require_permission() or public() to each: {offenders}"
    )
