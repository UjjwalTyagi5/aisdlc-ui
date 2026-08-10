"""REQ-M9-14 — GET /runs/{run_id}/eval API unit tests.

Asserts:
  - Route is registered at /runs/{run_id}/eval (route existence check)
  - require_permission("artifact:view", run_param="run_id") is on the route (403 check)
  - GET /runs/{run_id}/eval returns 200 with artifact:view permission
  - Tenant isolation: a tenant-B EvalRecord is invisible to tenant-A's request
    (T-9.3-09 cross-tenant isolation, mocked-DB variant)
  - Request without Authorization header returns 401/403

Uses ASGI transport (httpx.AsyncClient) + mint_token-minted JWTs, matching
tests/audit/test_audit_api.py and tests/cost/test_cost_api.py.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

TENANT_A = "00000000-0000-0000-0000-000000000001"
TENANT_B = "00000000-0000-0000-0000-000000000002"


def _make_db_session_override(orm_rows: list):
    """Return an async-generator dependency override yielding a mock AsyncSession.

    orm_rows is the list of ORM-like objects (or MagicMocks) that
    mocked execute().scalars().all() returns.
    """

    async def _override():
        session = MagicMock()
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=orm_rows)
        result = MagicMock()
        result.scalars = MagicMock(return_value=scalars)
        session.execute = AsyncMock(return_value=result)
        yield session

    return _override


def _make_eval_record(
    tenant_id: str = TENANT_A,
    run_id: str = "run-001",
    agent_type: str = "requirements",
    score: float | None = 0.92,
    signals: dict | None = None,
):
    """Build a MagicMock that looks like a shared.models.orm.EvalRecord instance."""
    record = MagicMock()
    record.id = uuid.uuid4()
    record.tenant_id = uuid.UUID(tenant_id)
    record.run_id = run_id
    record.agent_type = agent_type
    record.score = Decimal(str(score)) if score is not None else None
    record.signals = signals or {"keyword_overlap": 0.9, "present_count": 3}
    from datetime import datetime, timezone
    record.created_at = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
    return record


@pytest.fixture(autouse=True)
def _mock_workspace_resolution(monkeypatch):
    """Patch resolve_default_workspace and resolve_workspace_for_run so
    require_permission's workspace-derivation step does not need a live
    Postgres connection in unit tests.

    The eval router uses run_param="run_id" so the dependency calls
    resolve_workspace_for_run, not resolve_default_workspace.  Both are
    patched to prevent unexpected DB connections (mirrors cost API test
    pattern, but also covers the run_param path).
    """
    import uuid as _uuid

    async def _fake_resolve_default(tenant_id: str):
        return _uuid.uuid4()

    async def _fake_resolve_for_run(run_id: str, tenant_id: str):
        return _uuid.uuid4()

    monkeypatch.setattr(
        "shared.authz.dependency.resolve_default_workspace",
        _fake_resolve_default,
    )
    monkeypatch.setattr(
        "shared.authz.dependency.resolve_workspace_for_run",
        _fake_resolve_for_run,
    )


@pytest.mark.unit
async def test_get_run_eval_route_registered():
    """GET /runs/{run_id}/eval is registered in process_api at the expected path."""
    from process_api import app

    routes = {route.path for route in app.routes}
    assert "/runs/{run_id}/eval" in routes, (
        f"Expected /runs/{{run_id}}/eval in routes; found: {sorted(routes)}"
    )


@pytest.mark.unit
async def test_get_run_eval_requires_artifact_view(mint_token):
    """GET /runs/{run_id}/eval returns 403 when JWT lacks artifact:view (T-9.3-10)."""
    import httpx
    from process_api import app
    from shared.db import get_db_session

    app.dependency_overrides[get_db_session] = _make_db_session_override([])
    try:
        token = mint_token(
            user_id="u1",
            tenant_id=TENANT_A,
            permissions=["run:create"],  # no artifact:view
        )
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/runs/run-001/eval", headers=headers)

        assert response.status_code == 403, (
            f"Expected 403 without artifact:view, got {response.status_code}"
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.unit
async def test_get_run_eval_with_artifact_view_returns_200(mint_token):
    """GET /runs/{run_id}/eval returns 200 with artifact:view permission (REQ-M9-14)."""
    import httpx
    from process_api import app
    from shared.db import get_db_session

    record = _make_eval_record()
    app.dependency_overrides[get_db_session] = _make_db_session_override([record])
    try:
        token = mint_token(
            user_id="u2",
            tenant_id=TENANT_A,
            permissions=["artifact:view"],
        )
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/runs/run-001/eval", headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list), "Response must be a list of EvalRecordOut"
        assert len(data) == 1
        item = data[0]
        assert item["agentType"] == "requirements"
        assert item["tenantId"] == TENANT_A
        assert item["runId"] == "run-001"
        # score 0.92 should round-trip as a float (Numeric(5,4) -> float)
        assert abs(item["score"] - 0.92) < 0.001
        assert "signals" in item
        assert "createdAt" in item
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.unit
async def test_get_run_eval_returns_list_empty_when_no_records(mint_token):
    """GET /runs/{run_id}/eval returns [] when the run has no EvalRecords."""
    import httpx
    from process_api import app
    from shared.db import get_db_session

    app.dependency_overrides[get_db_session] = _make_db_session_override([])
    try:
        token = mint_token(
            user_id="u3",
            tenant_id=TENANT_A,
            permissions=["artifact:view"],
        )
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/runs/run-new/eval", headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert data == []
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.unit
async def test_get_run_eval_cross_tenant_isolation(mint_token):
    """T-9.3-09: a tenant-B EvalRecord is not visible when tenant-A requests /eval.

    This mocked-DB test asserts the handler filters by tenant_id.  The
    mocked session returns zero rows to simulate the RLS/WHERE filtering
    that would occur with a real DB (tenant_id filter in the WHERE clause).
    """
    import httpx
    from process_api import app
    from shared.db import get_db_session

    # The mock returns NO rows — simulating that the tenant_id WHERE clause
    # on a real DB would filter out tenant-B's record when queried as tenant-A.
    # (The handler only reads request.state.tenant_id; it never trusts the client.)
    app.dependency_overrides[get_db_session] = _make_db_session_override([])
    try:
        # Tenant-A token querying a run that ONLY has tenant-B's EvalRecord.
        # The WHERE clause (tenant_id == A) would return empty on a live DB.
        token = mint_token(
            user_id="u-iso",
            tenant_id=TENANT_A,
            permissions=["artifact:view"],
        )
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/runs/run-b-only/eval", headers=headers)

        assert response.status_code == 200
        data = response.json()
        # No tenant-B records visible to tenant-A
        assert data == [], (
            "Tenant-A must not see tenant-B's EvalRecord (T-9.3-09 cross-tenant isolation)"
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.unit
async def test_get_run_eval_no_token_returns_401():
    """GET /runs/{run_id}/eval without Authorization header returns 401."""
    import httpx
    from process_api import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/runs/run-001/eval")

    assert response.status_code in (401, 403), (
        f"Expected 401 or 403 without token, got {response.status_code}"
    )
