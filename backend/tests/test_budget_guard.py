"""Hierarchical budget enforcement tests (live Postgres).

Covers the durable rollup (record_usage_rollup / read_scope_spend), the scope-chain
resolution, and check_budgets blocking at each level of org ⊇ workspace ⊇ project.
"""
import uuid

import pytest
from sqlalchemy import text

from shared.db import get_db_session_for_tenant


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


def _clear_caches():
    from shared.services import budget_store
    from shared.services.budget_guard import clear_budget_cache
    budget_store._CHAIN_CACHE.clear()
    clear_budget_cache()


async def _seed_hierarchy(tenant, *, org_budget=None, ws_budget=None, proj_budget=None):
    ws = str(uuid.uuid4())
    proj = str(uuid.uuid4())
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(
            text("INSERT INTO organizations (id, slug, display_name, monthly_budget_usd) "
                 "VALUES (:id, :slug, :dn, :b)"),
            {"id": tenant, "slug": f"o-{tenant[:8]}", "dn": "Org", "b": org_budget},
        )
        await s.execute(
            text("INSERT INTO workspaces (id, organization_id, slug, display_name, monthly_budget_usd) "
                 "VALUES (:id, :o, :slug, :dn, :b)"),
            {"id": ws, "o": tenant, "slug": f"w-{ws[:8]}", "dn": "WS", "b": ws_budget},
        )
        await s.execute(
            text("INSERT INTO projects (id, workspace_id, tenant_id, display_name, monthly_budget_usd) "
                 "VALUES (:id, :w, :t, :dn, :b)"),
            {"id": proj, "w": ws, "t": tenant, "dn": "P", "b": proj_budget},
        )
        await s.commit()
    return ws, proj


async def _cleanup(tenant):
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(text("DELETE FROM usage_monthly WHERE tenant_id=:t"), {"t": tenant})
        await s.execute(text("DELETE FROM projects WHERE tenant_id=:t"), {"t": tenant})
        await s.execute(text("DELETE FROM workspaces WHERE organization_id=:t"), {"t": tenant})
        await s.execute(text("DELETE FROM organizations WHERE id=:t"), {"t": tenant})
        await s.commit()


@pytest.mark.asyncio
async def test_scope_chain_with_and_without_project():
    from shared.services.budget_store import resolve_scope_chain
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, proj = await _seed_hierarchy(tenant)
    try:
        chain = await resolve_scope_chain(tenant, proj)
        assert ("org", tenant) in chain
        assert ("workspace", ws) in chain
        assert ("project", proj) in chain
        # No project → org only.
        assert await resolve_scope_chain(tenant, None) == [("org", tenant)]
    finally:
        await _cleanup(tenant)


@pytest.mark.asyncio
async def test_rollup_accumulates_across_scopes():
    from shared.services.budget_store import read_scope_spend, record_usage_rollup
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, proj = await _seed_hierarchy(tenant)
    try:
        await record_usage_rollup(tenant, proj, cost_usd=0.03, tokens=100)
        await record_usage_rollup(tenant, proj, cost_usd=0.02, tokens=50)
        # Each scope in the chain accumulates the same call cost.
        assert round(await read_scope_spend(tenant, "project", proj), 4) == 0.05
        assert round(await read_scope_spend(tenant, "workspace", ws), 4) == 0.05
        assert round(await read_scope_spend(tenant, "org", tenant), 4) == 0.05
    finally:
        await _cleanup(tenant)


@pytest.mark.asyncio
async def test_org_budget_blocks_over_and_allows_under():
    from shared.services.budget_guard import BudgetExceededError, check_budgets
    from shared.services.budget_store import record_usage_rollup
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, proj = await _seed_hierarchy(tenant, org_budget=0.10)
    try:
        await record_usage_rollup(tenant, proj, cost_usd=0.05, tokens=100)
        _clear_caches()
        await check_budgets(tenant, proj)  # under 0.10 → allowed
        await record_usage_rollup(tenant, proj, cost_usd=0.06, tokens=100)  # now 0.11
        _clear_caches()
        with pytest.raises(BudgetExceededError) as ei:
            await check_budgets(tenant, proj)
        assert ei.value.scope == "org"
    finally:
        await _cleanup(tenant)


@pytest.mark.asyncio
async def test_workspace_budget_blocks():
    from shared.services.budget_guard import BudgetExceededError, check_budgets
    from shared.services.budget_store import record_usage_rollup
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, proj = await _seed_hierarchy(tenant, ws_budget=0.05)
    try:
        await record_usage_rollup(tenant, proj, cost_usd=0.06, tokens=100)
        _clear_caches()
        with pytest.raises(BudgetExceededError) as ei:
            await check_budgets(tenant, proj)
        assert ei.value.scope == "workspace"
    finally:
        await _cleanup(tenant)


@pytest.mark.asyncio
async def test_project_budget_blocks():
    from shared.services.budget_guard import BudgetExceededError, check_budgets
    from shared.services.budget_store import record_usage_rollup
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, proj = await _seed_hierarchy(tenant, proj_budget=0.02)
    try:
        await record_usage_rollup(tenant, proj, cost_usd=0.03, tokens=100)
        _clear_caches()
        with pytest.raises(BudgetExceededError) as ei:
            await check_budgets(tenant, proj)
        assert ei.value.scope == "project"
    finally:
        await _cleanup(tenant)


@pytest.mark.asyncio
async def test_null_budget_never_blocks():
    from shared.services.budget_guard import check_budgets
    from shared.services.budget_store import record_usage_rollup
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, proj = await _seed_hierarchy(tenant)  # no budgets anywhere
    try:
        await record_usage_rollup(tenant, proj, cost_usd=999.0, tokens=10_000)
        _clear_caches()
        await check_budgets(tenant, proj)  # unlimited → no raise
    finally:
        await _cleanup(tenant)
