"""REQ-M9-07 — GET /cost aggregate query API unit tests.

Asserts:
  - GET /cost requires cost:view (Phase 6: was artifact:view, tightened to cost:view
    per RBAC matrix — developer/pm/qa/architect lose cost access; 403 without it)
  - GET /cost returns per-(agent_type, model) aggregates + totals scoped to
    request.state.tenant_id, with window_days bounded 1..365
  - A tenant-B seeded agent_call_logs row is absent from tenant-A's response
    (T-9.2-05 cross-tenant isolation; live-DB integration test, skipped
    without POSTGRES_CONN_STRING — mirrors tests/audit/test_append_only.py)

Uses ASGI transport (httpx.AsyncClient) + mint_token-minted JWTs, matching
tests/audit/test_audit_api.py.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

TENANT_A = "00000000-0000-0000-0000-000000000001"
TENANT_B = "00000000-0000-0000-0000-000000000002"


def _make_db_session_override(rows: list[tuple]):
    """Return an async-generator dependency override yielding a mock AsyncSession.

    `rows` is the list of tuples the mocked execute().all() returns — one
    per (model, input_tokens, output_tokens, cost_usd, call_count).
    """

    async def _override():
        session = MagicMock()
        result = MagicMock()
        result.all = MagicMock(return_value=rows)
        result.scalar = MagicMock(return_value=None)  # org-budget query → env fallback
        session.execute = AsyncMock(return_value=result)
        yield session

    return _override


@pytest.fixture(autouse=True)
def _mock_workspace_resolution(monkeypatch):
    """Patch active_workspace_for_request so require_permission's RBAC check does not
    require a live Postgres connection in unit tests (route logic, not workspace
    resolution, is under test here). The cross-tenant isolation test below is
    @pytest.mark.integration and skipped without POSTGRES_CONN_STRING, so it
    exercises the real DB path including workspace resolution.

    (Was patching shared.authz.dependency.resolve_default_workspace, which the
    sdlc_product merge replaced with active_workspace_for_request — D-06 selector.)
    """
    import uuid as _uuid

    async def _fake_active_workspace(request, tenant_id):
        request.state.workspace_id = _uuid.uuid4()
        return request.state.workspace_id

    monkeypatch.setattr(
        "shared.authz.dependency.active_workspace_for_request",
        _fake_active_workspace,
    )


@pytest.mark.unit
async def test_get_cost_requires_cost_view(mint_token):
    """GET /cost returns 403 when JWT lacks cost:view permission (Phase 6: was artifact:view)."""
    import httpx
    from process_api import app
    from shared.db import get_db_session

    app.dependency_overrides[get_db_session] = _make_db_session_override([])
    try:
        token = mint_token(
            user_id="u1",
            tenant_id=TENANT_A,
            permissions=["run:create"],  # no cost:view (or artifact:view — either way denied)
        )
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/cost", headers=headers)

        assert response.status_code == 403, (
            f"Expected 403 without cost:view, got {response.status_code}"
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.unit
async def test_get_cost_returns_aggregates_and_totals(mint_token, monkeypatch):
    """GET /cost returns per-model rows + grand totals, sourced from Langfuse metrics."""
    import httpx
    from process_api import app
    from shared.db import get_db_session

    # Cost is Langfuse-sourced now: enable it and mock the /api/public/metrics/daily feed.
    import config.env as _env
    import shared.routers.traces as _traces
    monkeypatch.setattr(_env, "ENABLE_LANGFUSE", True)

    async def _fake_lf_get(path, params):
        assert path == "/api/public/metrics/daily"
        assert any(t.startswith("tenant:") for t in params.get("tags", []))
        return {"data": [{"date": "2026-07-09", "totalCost": 0.045, "usage": [
            {"model": "claude-sonnet-4-6", "inputUsage": 1000, "outputUsage": 500,
             "totalCost": 0.015, "countObservations": 3},
            {"model": "claude-opus-4-8", "inputUsage": 2000, "outputUsage": 1000,
             "totalCost": 0.030, "countObservations": 2},
            {"model": None, "inputUsage": 0, "outputUsage": 0, "totalCost": 0,
             "countObservations": 6},  # non-LLM bucket — must be skipped
        ]}]}
    monkeypatch.setattr(_traces, "_lf_get", _fake_lf_get)

    app.dependency_overrides[get_db_session] = _make_db_session_override([])
    try:
        token = mint_token(
            user_id="u2",
            tenant_id=TENANT_A,
            # settings:manage makes this caller ORG-WIDE, which is what a tenant-wide
            # total now requires. `cost:view` alone says the caller may see spend, not
            # whose: with no bindings they are scoped to an empty set of units and get
            # zeroes — correctly, since this test asserts the aggregation math rather
            # than the scope filter. See docs/rbac-audit-2026-08-17.md finding 4.
            permissions=["artifact:view", "cost:view", "settings:manage"],
        )
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/cost", headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert data["windowDays"] == 30
        assert len(data["rows"]) == 2  # null-model bucket skipped
        by_model = {r["model"]: r for r in data["rows"]}
        assert by_model["claude-sonnet-4-6"]["callCount"] == 3
        assert "agentType" not in data["rows"][0]
        # totals = 0.015 + 0.030 = 0.045
        assert round(data["totalCostUsd"], 6) == 0.045
        assert data["totalInputTokens"] == 3000
        assert data["totalOutputTokens"] == 1500
        assert "generatedAt" in data
        # budget signal fields present (REQ-M9-09)
        assert "budgetUsd" in data
        assert "utilization" in data
        assert "breached80" in data
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.unit
async def test_get_cost_window_days_bounds(mint_token):
    """window_days is bounded 1..365 — out-of-range values are rejected (422)."""
    import httpx
    from process_api import app
    from shared.db import get_db_session

    app.dependency_overrides[get_db_session] = _make_db_session_override([])
    try:
        token = mint_token(
            user_id="u3",
            tenant_id=TENANT_A,
            # Phase 6: tightened to cost:view. process_api _VIEW_DEP also requires
            # artifact:view — real roles (delivery_lead, security_auditor) carry both.
            permissions=["artifact:view", "cost:view"],
        )
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            too_big = await client.get("/cost?window_days=400", headers=headers)
            too_small = await client.get("/cost?window_days=0", headers=headers)
            ok = await client.get("/cost?window_days=7", headers=headers)

        assert too_big.status_code == 422
        assert too_small.status_code == 422
        assert ok.status_code == 200
        assert ok.json()["windowDays"] == 7
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.unit
async def test_get_cost_no_token_returns_401():
    """GET /cost without Authorization header returns 401."""
    import httpx
    from process_api import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/cost")

    assert response.status_code in (401, 403), (
        f"Expected 401 or 403 without token, got {response.status_code}"
    )


@pytest.mark.unit
async def test_get_cost_tenant_tag_is_server_derived(mint_token, monkeypatch):
    """T-9.2-05: GET /cost is scoped by the CALLER'S tenant tag, never a supplied one.

    Rewritten from a live-DB test that seeded two tenants' `agent_call_logs` rows and
    asserted tenant A's total stayed under tenant B's 999.0. That stopped testing
    anything the day /cost moved onto Langfuse: the endpoint no longer reads
    agent_call_logs at all, so the total was 0.0 and `0.0 < 999.0` passed without
    exercising a single isolation code path.

    The boundary that actually holds now is the tag on the outbound Langfuse query —
    application-level, not Postgres RLS, because Langfuse is one shared project. So
    that is what this asserts: the tenant tag matches the token's tenant, and query
    parameters a caller controls cannot introduce another tenant's tag.
    """
    import httpx
    from process_api import app
    from shared.db import get_db_session

    import config.env as _env
    import shared.routers.traces as _traces
    monkeypatch.setattr(_env, "ENABLE_LANGFUSE", True)

    seen: list[list[str]] = []

    async def _capture_lf_get(path, params):
        seen.append(list(params.get("tags") or []))
        return {"data": []}

    monkeypatch.setattr(_traces, "_lf_get", _capture_lf_get)

    app.dependency_overrides[get_db_session] = _make_db_session_override([])
    try:
        token = mint_token(
            user_id="u-iso",
            tenant_id=TENANT_A,
            permissions=["artifact:view", "cost:view", "settings:manage"],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                # A caller-supplied tenant tag must not reach the Langfuse query.
                f"/cost?window_days=1&tags=tenant:{TENANT_B}",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 200
        assert seen, "expected at least one Langfuse query"
        for tags in seen:
            assert f"tenant:{TENANT_A}" in tags
            assert not any(TENANT_B in t for t in tags)
    finally:
        app.dependency_overrides.pop(get_db_session, None)
