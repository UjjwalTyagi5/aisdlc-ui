"""GET /cost-summary?project_id=X (task #22 remainder) — one project's current-month
spend against its effective budget, sourced from the same usage_monthly rollup
GET /budgets already reads. Live-DB integration tests (project rows are FORCE RLS)."""
from __future__ import annotations

import uuid

import pytest

from config.env import POSTGRES_CONN_STRING

_skip_no_db = pytest.mark.skipif(
    not POSTGRES_CONN_STRING,
    reason="POSTGRES_CONN_STRING not set — skipping live-DB test",
)


async def _seed_usage(tenant_id: str, project_id: str, cost_usd: float) -> None:
    from sqlalchemy import text

    from shared.db import get_db_session_for_tenant
    from shared.services.budget_store import month_key

    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(
            text(
                "INSERT INTO usage_monthly (id, tenant_id, scope, scope_id, month, cost_usd, total_tokens, updated_at) "
                "VALUES (:id, :t, 'project', :sid, :m, :cost, 0, now()) "
                "ON CONFLICT (tenant_id, scope, scope_id, month) DO UPDATE SET cost_usd = :cost"
            ),
            {"id": str(uuid.uuid4()), "t": tenant_id, "sid": project_id, "m": month_key(), "cost": cost_usd},
        )


@pytest.mark.integration
@_skip_no_db
@pytest.mark.asyncio
async def test_cost_summary_returns_project_spend_and_budget(mint_token):
    import httpx
    from process_api import app
    from sqlalchemy import text

    from shared.db import get_db_session_for_tenant
    from tests.test_model_grants import _seed_org_workspace_project

    tenant = str(uuid.uuid4())
    try:
        _, proj_id = await _seed_org_workspace_project(tenant, "Unit A")
        async with get_db_session_for_tenant(tenant) as s:
            await s.execute(
                text("UPDATE projects SET monthly_budget_usd = 100 WHERE id = :id"),
                {"id": proj_id},
            )
        await _seed_usage(tenant, proj_id, 25.5)
    except Exception as exc:
        pytest.skip(f"DB not reachable or setup incomplete: {exc}")

    token = mint_token(tenant_id=tenant, permissions=["artifact:view", "cost:view"])
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/cost/summary", params={"project_id": proj_id}, headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["projectId"] == str(proj_id)
    assert body["monthlySpendUsd"] == 25.5
    assert body["monthlyBudgetUsd"] == 100.0
    assert body["utilization"] == pytest.approx(0.255)
    assert body["breached80"] is False


@pytest.mark.integration
@_skip_no_db
@pytest.mark.asyncio
async def test_cost_summary_unknown_project_is_404(mint_token):
    import httpx
    from process_api import app

    tenant = str(uuid.uuid4())
    token = mint_token(tenant_id=tenant, permissions=["artifact:view", "cost:view"])
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/cost/summary", params={"project_id": str(uuid.uuid4())}, headers=headers,
        )
    assert resp.status_code == 404


@pytest.mark.integration
@_skip_no_db
@pytest.mark.asyncio
async def test_cost_summary_requires_cost_view_permission(mint_token):
    import httpx
    from process_api import app

    from tests.test_model_grants import _seed_org_workspace_project

    tenant = str(uuid.uuid4())
    try:
        _, proj_id = await _seed_org_workspace_project(tenant, "Unit A")
    except Exception as exc:
        pytest.skip(f"DB not reachable or setup incomplete: {exc}")

    token = mint_token(tenant_id=tenant, permissions=[])
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/cost/summary", params={"project_id": proj_id}, headers=headers)
    assert resp.status_code == 403
