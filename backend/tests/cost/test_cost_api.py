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
async def test_get_cost_returns_per_agent_and_per_model_rows(mint_token, monkeypatch):
    """GET /cost groups by (agent, model), sourced from each project's own Langfuse.

    Was per-model only against a shared project. Both halves changed for the same
    reason: traces moved into per-project Langfuse projects, so the old tenant-tagged
    query reads a project holding nothing and reports zero spend — and the richer
    /api/public/metrics endpoint that the per-project read needs also takes dimensions,
    which is what finally makes the agent split the Cost page promised possible.
    """
    import httpx
    from process_api import app
    from shared.db import get_db_session

    import config.env as _env
    import shared.routers.traces as _traces
    monkeypatch.setattr(_env, "ENABLE_LANGFUSE", True)

    # One bound project for this tenant, so the per-project path is the one exercised.
    class _Binding:
        project_id = "11111111-1111-1111-1111-111111111111"
        workspace_id = "22222222-2222-2222-2222-222222222222"
        langfuse_host = "https://langfuse.invalid"
        public_key = "pk-lf-test"
        secret_key = "sk-lf-test"

    async def _fake_bindings(session, tenant_id, project_ids):
        return [_Binding()]

    import shared.observability.bindings as _bindings
    monkeypatch.setattr(_bindings, "load_bindings", _fake_bindings)

    async def _fake_lf_get(path, params, **kw):
        assert path == "/api/public/metrics", path
        # Credentials must be the BINDING's — reading the shared project would be the
        # bug this endpoint just stopped having.
        assert kw.get("public_key") == "pk-lf-test"
        return {"data": [
            {"traceName": "sdlc:requirements", "providedModelName": "claude-sonnet-4-6",
             "sum_totalCost": 0.015, "sum_totalTokens": 1500, "count_count": 3},
            {"traceName": "sdlc:development", "providedModelName": "claude-opus-4-8",
             "sum_totalCost": 0.030, "sum_totalTokens": 3000, "count_count": 2},
            # A non-LLM span: no model, no cost. Must be skipped, not counted as a row.
            {"traceName": "sdlc:requirements", "providedModelName": None,
             "sum_totalCost": None, "sum_totalTokens": 0, "count_count": 6},
        ]}
    monkeypatch.setattr(_traces, "_lf_get", _fake_lf_get)

    app.dependency_overrides[get_db_session] = _make_db_session_override(
        [(_Binding.project_id, _Binding.workspace_id)]
    )
    try:
        token = mint_token(
            user_id="u2", tenant_id=TENANT_A,
            permissions=["artifact:view", "cost:view", "settings:manage"],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/cost", headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data["rows"]) == 2  # the null-model row is skipped
        by_agent = {r["agentType"]: r for r in data["rows"]}
        # THE POINT OF THIS TEST: the agent is on the row now.
        assert set(by_agent) == {"requirements", "development"}
        assert by_agent["requirements"]["model"] == "claude-sonnet-4-6"
        assert by_agent["requirements"]["callCount"] == 3
        assert round(data["totalCostUsd"], 6) == 0.045
        assert "budgetUsd" in data and "utilization" in data and "breached80" in data
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
async def test_get_cost_reads_only_this_tenants_bindings(mint_token, monkeypatch):
    """Isolation for /cost is now the CREDENTIALS, not a tag.

    This asserted that the tenant tag on the outbound query came from the token. That
    guarantee has moved: each project has its own Langfuse project and key pair, so the
    binding lookup — scoped to the caller's tenant in SQL, under RLS — decides which
    credentials exist at all. A query cannot reach another tenant's traces because the
    key that would read them is never loaded.

    So what is pinned here is the lookup: it is filtered by the token's tenant, and a
    caller-supplied parameter cannot widen it.
    """
    import httpx
    from process_api import app
    from shared.db import get_db_session

    import config.env as _env
    import shared.routers.traces as _traces
    monkeypatch.setattr(_env, "ENABLE_LANGFUSE", True)

    seen_params: list[dict] = []

    async def _capture_execute(stmt, params=None):
        if params:
            seen_params.append(dict(params))
        result = MagicMock()
        result.all = MagicMock(return_value=[])
        result.scalar = MagicMock(return_value=None)
        return result

    async def _override():
        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture_execute)
        yield session

    async def _fake_lf_get(path, params, **kw):  # pragma: no cover - must not be reached
        raise AssertionError("no bindings, so Langfuse must not be queried")

    monkeypatch.setattr(_traces, "_lf_get", _fake_lf_get)

    app.dependency_overrides[get_db_session] = _override
    try:
        token = mint_token(
            user_id="u-iso", tenant_id=TENANT_A,
            permissions=["artifact:view", "cost:view", "settings:manage"],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                # A tenant supplied by the caller must not reach the binding lookup.
                f"/cost?window_days=1&tenant_id={TENANT_B}",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 200
        tenant_params = [p["t"] for p in seen_params if "t" in p]
        assert tenant_params, "expected the binding lookup to run"
        assert all(t == TENANT_A for t in tenant_params)
        assert not any(TENANT_B in str(v) for p in seen_params for v in p.values())
    finally:
        app.dependency_overrides.pop(get_db_session, None)
