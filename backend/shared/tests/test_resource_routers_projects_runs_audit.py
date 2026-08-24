"""Tests for /projects, /runs, /audit resource routers (milestone-4, plan 02).

Covers:
  (a) 401 without a Bearer token on /projects, /runs, /audit (MUST pass — no DB required)
  (b) With a valid HS256 token, assert 200 + paginated envelope on /projects and /runs
  (c) Tenant scoping: token for tenant A cannot see tenant B rows

Tests use httpx AsyncClient + ASGITransport (same pattern as test_jwt_auth.py).

DB-required tests (b, c) skip cleanly when POSTGRES_CONN_STRING is not set so the
401-without-token assertion always runs in CI even without a Postgres container.

Threat mitigations verified:
  T-M4-01: tenant-scoped queries (test_tenant_isolation)
  T-M4-02: unauthenticated access rejected (test_*_requires_auth)
"""
import time
import uuid

import pytest
import pytest_asyncio

from config.env import JWT_SECRET_KEY, JWT_ALGORITHM, POSTGRES_CONN_STRING

_skip_no_jwt = pytest.mark.skipif(
    not JWT_SECRET_KEY,
    reason="JWT_SECRET_KEY not set — skipping resource router auth tests",
)

_skip_no_db = pytest.mark.skipif(
    not POSTGRES_CONN_STRING,
    reason="POSTGRES_CONN_STRING not set — skipping DB-dependent resource router tests",
)


async def _check_db_reachable() -> bool:
    """Probe Postgres once; return False if unreachable so DB tests can skip cleanly."""
    if not POSTGRES_CONN_STRING:
        return False
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text
        from sqlalchemy.pool import NullPool
        engine = create_async_engine(POSTGRES_CONN_STRING, poolclass=NullPool)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        return True
    except Exception:
        return False


def _mint_token(tenant_id: str | None = None, exp_offset: int = 3600) -> str:
    """Mint a signed HS256 token using the configured secret.

    tenant_id defaults to a fresh UUID, not a literal string: require_permission's
    workspace-resolution fallback (active_workspace_for_request) runs a real
    uuid.UUID(str(tenant_id)) against Postgres, and a non-UUID tenant_id 500s instead
    of the clean 404-then-fall-through-to-permission-check it's designed to produce
    for a tenant with no seeded workspace. permissions defaults to the wildcard since
    these tests exercise the routes' shape/tenant-scoping, not RBAC.
    """
    try:
        import jwt
        now = int(time.time())
        payload = {
            "sub": "test-user-001",
            "tenant_id": tenant_id or str(uuid.uuid4()),
            "iat": now,
            "exp": now + exp_offset,
            "permissions": ["admin:*"],
        }
        return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM or "HS256")
    except ImportError:
        pytest.skip("PyJWT not installed — cannot mint token")


# ── 401 tests (no DB required) ────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
@_skip_no_jwt
async def test_projects_requires_auth():
    """GET /projects without a Bearer token must return 401 (T-M4-02)."""
    from httpx import ASGITransport, AsyncClient
    from process_api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/projects")
    assert resp.status_code == 401, (
        f"Expected 401 without JWT on /projects, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
@_skip_no_jwt
async def test_runs_requires_auth():
    """GET /runs without a Bearer token must return 401 (T-M4-02)."""
    from httpx import ASGITransport, AsyncClient
    from process_api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/runs")
    assert resp.status_code == 401, (
        f"Expected 401 without JWT on /runs, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
@_skip_no_jwt
async def test_audit_requires_auth():
    """GET /audit without a Bearer token must return 401 (T-M4-02)."""
    from httpx import ASGITransport, AsyncClient
    from process_api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/audit")
    assert resp.status_code == 401, (
        f"Expected 401 without JWT on /audit, got {resp.status_code}: {resp.text}"
    )


# ── 200 + paginated envelope tests (requires DB) ─────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
@_skip_no_jwt
@_skip_no_db
async def test_projects_returns_paginated_envelope():
    """GET /projects with a valid JWT returns 200 + paginated envelope shape."""
    if not await _check_db_reachable():
        pytest.skip("Postgres unreachable — skipping DB-dependent test (POSTGRES_CONN_STRING set but DB not running)")

    from httpx import ASGITransport, AsyncClient
    from process_api import app

    token = _mint_token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/projects",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, (
        f"Expected 200 on /projects with JWT, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "items" in body, f"Response missing 'items': {body}"
    assert "pagination" in body, f"Response missing 'pagination': {body}"
    assert "page" in body["pagination"], f"pagination missing 'page': {body}"
    assert "pageSize" in body["pagination"], f"pagination missing 'pageSize': {body}"
    assert "total" in body["pagination"], f"pagination missing 'total': {body}"
    assert isinstance(body["items"], list), f"'items' is not a list: {body}"


@pytest.mark.unit
@pytest.mark.asyncio
@_skip_no_jwt
@_skip_no_db
async def test_runs_returns_paginated_envelope():
    """GET /runs with a valid JWT returns 200 + paginated envelope shape."""
    if not await _check_db_reachable():
        pytest.skip("Postgres unreachable — skipping DB-dependent test (POSTGRES_CONN_STRING set but DB not running)")

    from httpx import ASGITransport, AsyncClient
    from process_api import app

    token = _mint_token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/runs",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, (
        f"Expected 200 on /runs with JWT, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "items" in body, f"Response missing 'items': {body}"
    assert "pagination" in body, f"Response missing 'pagination': {body}"


# ── Tenant isolation tests (requires DB) ─────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
@_skip_no_jwt
@_skip_no_db
async def test_tenant_isolation_projects():
    """Token for tenant A cannot see tenant B rows (T-M4-01).

    Seed one Project row for tenant A and assert that a request authenticated
    as tenant B returns 0 items.
    """
    if not await _check_db_reachable():
        pytest.skip("Postgres unreachable — skipping DB-dependent test (POSTGRES_CONN_STRING set but DB not running)")

    from httpx import ASGITransport, AsyncClient
    from shared.db import get_db_session_for_tenant, get_db_session_superuser
    from shared.models.orm import Organization, Project, Workspace

    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    workspace_id = uuid.uuid4()

    # organizations/workspaces are global (no RLS) — organization.id IS the tenant_id
    # (see test_development_agent_chat_access.py's project_with_org_admin fixture for
    # the same pattern). A real row is required: projects.workspace_id is FK'd to
    # workspaces.id, so an arbitrary UUID here fails the FK constraint outright.
    async with get_db_session_superuser() as session:
        session.add(Organization(id=uuid.UUID(tenant_a), slug=f"tenant-a-{tenant_a[:8]}", display_name="Tenant A"))
        session.add(Workspace(id=workspace_id, organization_id=uuid.UUID(tenant_a), slug="unit", display_name="Unit"))

    # GET /projects filters through visible_project_ids (role-binding reach), not just
    # RLS — a permissions claim on the JWT is not a role binding. Without this, "test
    # -user-001" has org_admin's permission wildcard but no bound scope, so the
    # visibility query returns [] and even tenant A can't see its own project.
    from shared.authz.grant import grant_role
    await grant_role(
        "test-user-001", uuid.UUID(tenant_a), "org_admin",
        tenant_id=tenant_a, scope_kind="organization", granted_by="test",
    )

    # Seed one project for tenant A. Must go through get_db_session_for_tenant, not a
    # raw engine session — it SETs LOCAL app.current_tenant_id, which RLS's WITH CHECK
    # on the projects table requires to admit the INSERT at all (a raw session with no
    # GUC set is rejected outright: "new row violates row-level security policy").
    async with get_db_session_for_tenant(tenant_a) as session:
        proj = Project(
            workspace_id=workspace_id,
            tenant_id=uuid.UUID(tenant_a),
            display_name="Tenant A Project",
            archived=False,
        )
        session.add(proj)
        await session.flush()
        project_id = str(proj.id)

    try:
        from process_api import app

        # Tenant B token — must not see tenant A rows
        token_b = _mint_token(tenant_id=tenant_b)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/projects",
                headers={"Authorization": f"Bearer {token_b}"},
            )
        assert resp.status_code == 200, f"Expected 200 for tenant B request, got {resp.status_code}"
        body = resp.json()
        project_ids = [p["id"] for p in body["items"]]
        assert project_id not in project_ids, (
            f"Tenant isolation FAILED: tenant B can see tenant A project {project_id}"
        )

        # Tenant A token — must see the row
        token_a = _mint_token(tenant_id=tenant_a)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp_a = await client.get(
                "/projects",
                headers={"Authorization": f"Bearer {token_a}"},
            )
        assert resp_a.status_code == 200
        body_a = resp_a.json()
        project_ids_a = [p["id"] for p in body_a["items"]]
        assert project_id in project_ids_a, (
            f"Tenant A should see its own project {project_id}, but it's missing: {project_ids_a}"
        )
    finally:
        # Cleanup: same tenant-scoped session — RLS applies to DELETE too. Organization/
        # workspace are superuser-scoped (no RLS) and must go after the project, since
        # projects.workspace_id/organizations FKs would otherwise block their deletion.
        from sqlalchemy import delete as sa_delete
        async with get_db_session_for_tenant(tenant_a) as session:
            await session.execute(sa_delete(Project).where(Project.id == uuid.UUID(project_id)))
        async with get_db_session_superuser() as session:
            await session.execute(sa_delete(Workspace).where(Workspace.id == workspace_id))
            await session.execute(sa_delete(Organization).where(Organization.id == uuid.UUID(tenant_a)))


@pytest.mark.unit
@pytest.mark.asyncio
@_skip_no_jwt
@_skip_no_db
async def test_project_detail_cross_tenant_returns_404():
    """GET /projects/{id} for a project owned by another tenant returns 404 (T-M4-01)."""
    if not await _check_db_reachable():
        pytest.skip("Postgres unreachable — skipping DB-dependent test (POSTGRES_CONN_STRING set but DB not running)")

    from httpx import ASGITransport, AsyncClient
    from shared.db import get_db_session_for_tenant, get_db_session_superuser
    from shared.models.orm import Organization, Project, Workspace

    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    workspace_id = uuid.uuid4()

    # organizations/workspaces are global (no RLS) — organization.id IS the tenant_id.
    # A real row is required: projects.workspace_id is FK'd to workspaces.id.
    async with get_db_session_superuser() as session:
        session.add(Organization(id=uuid.UUID(tenant_a), slug=f"tenant-a-{tenant_a[:8]}", display_name="Tenant A"))
        session.add(Workspace(id=workspace_id, organization_id=uuid.UUID(tenant_a), slug="unit", display_name="Unit"))

    # get_db_session_for_tenant SETs LOCAL app.current_tenant_id, which RLS's WITH
    # CHECK on the projects table requires to admit the INSERT — see the identical
    # note in test_tenant_isolation_projects above.
    async with get_db_session_for_tenant(tenant_a) as session:
        proj = Project(
            workspace_id=workspace_id,
            tenant_id=uuid.UUID(tenant_a),
            display_name="Tenant A Secret Project",
            archived=False,
        )
        session.add(proj)
        await session.flush()
        project_id = str(proj.id)

    try:
        from process_api import app

        # Tenant B cannot read tenant A project
        token_b = _mint_token(tenant_id=tenant_b)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/projects/{project_id}",
                headers={"Authorization": f"Bearer {token_b}"},
            )
        assert resp.status_code == 404, (
            f"Expected 404 for cross-tenant project access, got {resp.status_code}: {resp.text}"
        )
    finally:
        from sqlalchemy import delete as sa_delete
        async with get_db_session_for_tenant(tenant_a) as session:
            await session.execute(sa_delete(Project).where(Project.id == uuid.UUID(project_id)))
        async with get_db_session_superuser() as session:
            await session.execute(sa_delete(Workspace).where(Workspace.id == workspace_id))
            await session.execute(sa_delete(Organization).where(Organization.id == uuid.UUID(tenant_a)))
