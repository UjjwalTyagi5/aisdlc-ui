"""Hierarchical budget enforcement tests (live Postgres).

Covers the durable rollup (record_usage_rollup / read_scope_spend), the scope-chain
resolution, and check_budgets blocking at each level of org ⊇ workspace ⊇ project.
"""
import uuid
from datetime import date as _date

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


# ── Allocation guards: what an UNSET org budget means (budget_alloc) ──────────
#
# These sit alongside the runtime-spend tests above because the two modules answer
# the same question at different moments — check_budgets asks "may this run spend",
# assert_*_fits asks "may this budget be set" — and they disagreed about NULL.
# budget_guard has always read NULL as "no cap"; budget_alloc fell back to
# DEFAULT_ORG_BUDGET_USD, so an org that never set a budget silently got a $100
# ceiling and could hold two default workspaces before refusing a third.


@pytest.mark.asyncio
async def test_an_unset_org_budget_caps_nothing():
    """THE REPORTED BUG: workspaces far past DEFAULT_ORG_BUDGET_USD, org budget NULL.

    Creating a business unit here returned 409 "Budget low: the org budget is $100.00
    with $9,100.00 already allocated" — against a cap nobody had set.
    """
    from shared.services.budget_alloc import assert_workspace_fits
    tenant = str(uuid.uuid4())
    _clear_caches()
    # org_budget=None is the point; the workspace alone dwarfs the $100 default.
    await _seed_hierarchy(tenant, org_budget=None, ws_budget=9000)
    try:
        async with get_db_session_for_tenant(tenant) as s:
            await assert_workspace_fits(s, tenant, 50, on_create=True)  # must not raise
    finally:
        await _cleanup(tenant)


@pytest.mark.asyncio
async def test_an_explicit_org_budget_is_still_enforced():
    """The guard was narrowed, not removed — a deliberate cap still refuses."""
    from fastapi import HTTPException
    from shared.services.budget_alloc import assert_workspace_fits
    tenant = str(uuid.uuid4())
    _clear_caches()
    await _seed_hierarchy(tenant, org_budget=200, ws_budget=180)
    try:
        async with get_db_session_for_tenant(tenant) as s:
            with pytest.raises(HTTPException) as exc:
                await assert_workspace_fits(s, tenant, 50, on_create=True)
            assert exc.value.status_code == 409
            assert "Budget low" in str(exc.value.detail)
    finally:
        await _cleanup(tenant)


@pytest.mark.asyncio
async def test_clearing_the_org_budget_is_not_setting_it_below_children():
    """Removing a ceiling is not lowering it.

    Read through effective_cap this refused, comparing the $100 default against what
    the workspaces already committed.
    """
    from shared.services.budget_alloc import assert_org_covers_workspaces
    tenant = str(uuid.uuid4())
    _clear_caches()
    await _seed_hierarchy(tenant, org_budget=9500, ws_budget=9000)
    try:
        async with get_db_session_for_tenant(tenant) as s:
            await assert_org_covers_workspaces(s, tenant, None)  # must not raise
            # …but genuinely lowering it below the children still refuses.
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await assert_org_covers_workspaces(s, tenant, 100)
            assert exc.value.status_code == 409
    finally:
        await _cleanup(tenant)


@pytest.mark.asyncio
async def test_workspace_to_project_allocation_is_untouched():
    """Only the ORG level changed; the level below keeps its default fallback."""
    from fastapi import HTTPException
    from shared.services.budget_alloc import assert_project_fits
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, _proj = await _seed_hierarchy(tenant, org_budget=None, ws_budget=100, proj_budget=90)
    try:
        async with get_db_session_for_tenant(tenant) as s:
            with pytest.raises(HTTPException) as exc:
                await assert_project_fits(s, tenant, ws, 50, on_create=True)
            assert exc.value.status_code == 409
    finally:
        await _cleanup(tenant)


@pytest.mark.asyncio
async def test_spend_accumulates_across_months():
    """A budget is a TOTAL, so last month's spend still counts against it.

    Reading only month_key() meant an exhausted project unblocked itself when the
    calendar turned over — the cap behaved like an allowance that refilled.
    """
    from shared.services.budget_store import read_scope_spend
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, proj = await _seed_hierarchy(tenant)
    try:
        # Two months of history written straight to the rollup, so the test does not
        # depend on what today's date happens to be.
        async with get_db_session_for_tenant(tenant) as s:
            for month, cost in (("202601", 4.0), ("202602", 6.0)):
                await s.execute(
                    text("INSERT INTO usage_monthly "
                         "(id, tenant_id, scope, scope_id, month, cost_usd, total_tokens, updated_at) "
                         "VALUES (:id, :t, 'project', :sid, :m, :c, 0, now())"),
                    {"id": str(uuid.uuid4()), "t": tenant, "sid": proj, "m": month, "c": cost},
                )
            await s.commit()
        assert round(await read_scope_spend(tenant, "project", proj), 4) == 10.0
    finally:
        await _cleanup(tenant)


@pytest.mark.asyncio
async def test_an_exhausted_total_budget_stays_blocked():
    """The consequence of the above: spend from a PAST month still blocks a run."""
    from shared.services.budget_guard import BudgetExceededError, check_budgets
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, proj = await _seed_hierarchy(tenant, proj_budget=10)
    try:
        async with get_db_session_for_tenant(tenant) as s:
            await s.execute(
                text("INSERT INTO usage_monthly "
                     "(id, tenant_id, scope, scope_id, month, cost_usd, total_tokens, updated_at) "
                     "VALUES (:id, :t, 'project', :sid, '202601', 12.0, 0, now())"),
                {"id": str(uuid.uuid4()), "t": tenant, "sid": proj},
            )
            await s.commit()
        _clear_caches()
        with pytest.raises(BudgetExceededError):
            await check_budgets(tenant, proj)
    finally:
        await _cleanup(tenant)


@pytest.mark.asyncio
async def test_raising_an_exhausted_cap_resumes_from_what_was_already_spent():
    """The whole point of a total budget, in one test.

    $20 cap, $20 spent → blocked. Raise the cap to $30 and the run proceeds, with
    the $20 still counted: the project has $10 left, not a fresh $30. Spend is
    never reset or forgiven — only the ceiling moves.
    """
    from shared.services.budget_guard import BudgetExceededError, check_budgets
    from shared.services.budget_store import read_scope_spend
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, proj = await _seed_hierarchy(tenant, proj_budget=20)
    try:
        # Spend the cap, across two months to prove the month grain is irrelevant.
        async with get_db_session_for_tenant(tenant) as s:
            for month, cost in (("202601", 12.0), ("202602", 8.0)):
                await s.execute(
                    text("INSERT INTO usage_monthly "
                         "(id, tenant_id, scope, scope_id, month, cost_usd, total_tokens, updated_at) "
                         "VALUES (:id, :t, 'project', :sid, :m, :c, 0, now())"),
                    {"id": str(uuid.uuid4()), "t": tenant, "sid": proj, "m": month, "c": cost},
                )
            await s.commit()
        _clear_caches()
        assert round(await read_scope_spend(tenant, "project", proj), 2) == 20.0

        # At the cap → blocked.
        with pytest.raises(BudgetExceededError):
            await check_budgets(tenant, proj)

        # Raise 20 → 30. This is what approving a budget_increase does.
        async with get_db_session_for_tenant(tenant) as s:
            await s.execute(
                text("UPDATE projects SET monthly_budget_usd = 30 WHERE id = CAST(:p AS uuid)"),
                {"p": proj},
            )
            await s.commit()
        _clear_caches()

        # Proceeds now — and the $20 is still on the clock.
        await check_budgets(tenant, proj)
        assert round(await read_scope_spend(tenant, "project", proj), 2) == 20.0

        # Spending the remaining $10 exhausts it again at $30, not at $50.
        async with get_db_session_for_tenant(tenant) as s:
            await s.execute(
                text("INSERT INTO usage_monthly "
                     "(id, tenant_id, scope, scope_id, month, cost_usd, total_tokens, updated_at) "
                     "VALUES (:id, :t, 'project', :sid, '202603', 10.0, 0, now())"),
                {"id": str(uuid.uuid4()), "t": tenant, "sid": proj},
            )
            await s.commit()
        _clear_caches()
        assert round(await read_scope_spend(tenant, "project", proj), 2) == 30.0
        with pytest.raises(BudgetExceededError):
            await check_budgets(tenant, proj)
    finally:
        await _cleanup(tenant)


# ── budget validity window (migration 0035) ──────────────────────────────────


def _d(value):
    """`YYYY-MM-DD` -> date. asyncpg binds a DATE parameter as a date object; the
    CAST in the statement does not make a string acceptable."""
    return _date.fromisoformat(value) if value else None


async def _set_window(tenant: str, project_id: str, start=None, end=None) -> None:
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(
            text("UPDATE projects SET budget_start_date = :a, budget_end_date = :b "
                 "WHERE id = CAST(:p AS uuid)"),
            {"a": _d(start), "b": _d(end), "p": project_id},
        )
        await s.commit()


@pytest.mark.asyncio
async def test_no_window_behaves_exactly_as_before():
    """The common case. A project with no dates must be untouched by any of this."""
    from shared.services.budget_guard import check_budgets
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, proj = await _seed_hierarchy(tenant, proj_budget=100)
    try:
        await check_budgets(tenant, proj)  # must not raise
    finally:
        await _cleanup(tenant)


@pytest.mark.asyncio
async def test_an_expired_window_blocks_even_under_the_cap():
    """It must be the DATE that refuses, not the amount.

    Nothing has been spent here, so a failure can only come from the window.
    """
    from shared.services.budget_guard import BudgetWindowClosedError, check_budgets
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, proj = await _seed_hierarchy(tenant, proj_budget=100)
    try:
        await _set_window(tenant, proj, start="2020-01-01", end="2020-12-31")
        _clear_caches()
        with pytest.raises(BudgetWindowClosedError) as exc:
            await check_budgets(tenant, proj)
        assert exc.value.state == "expired"
        assert "2020-12-31" in str(exc.value)
    finally:
        await _cleanup(tenant)


@pytest.mark.asyncio
async def test_a_window_that_has_not_started_blocks():
    from shared.services.budget_guard import BudgetWindowClosedError, check_budgets
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, proj = await _seed_hierarchy(tenant, proj_budget=100)
    try:
        await _set_window(tenant, proj, start="2099-01-01", end="2099-12-31")
        _clear_caches()
        with pytest.raises(BudgetWindowClosedError) as exc:
            await check_budgets(tenant, proj)
        assert exc.value.state == "scheduled"
    finally:
        await _cleanup(tenant)


@pytest.mark.asyncio
async def test_inside_the_window_the_ordinary_cap_rules_apply():
    """Active window: the budget behaves exactly as it does with no window at all."""
    from shared.services.budget_guard import BudgetExceededError, check_budgets
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, proj = await _seed_hierarchy(tenant, proj_budget=10)
    try:
        await _set_window(tenant, proj, start="2020-01-01", end="2099-12-31")
        _clear_caches()
        await check_budgets(tenant, proj)  # under the cap, inside the window → fine

        async with get_db_session_for_tenant(tenant) as s:
            await s.execute(
                text("INSERT INTO usage_monthly "
                     "(id, tenant_id, scope, scope_id, month, cost_usd, total_tokens, updated_at) "
                     "VALUES (:id, :t, 'project', :sid, '202601', 12.0, 0, now())"),
                {"id": str(uuid.uuid4()), "t": tenant, "sid": proj},
            )
            await s.commit()
        _clear_caches()
        # Over the cap inside the window → the SPEND refuses, not the date.
        with pytest.raises(BudgetExceededError):
            await check_budgets(tenant, proj)
    finally:
        await _cleanup(tenant)


@pytest.mark.asyncio
async def test_a_window_without_a_budget_blocks_nothing():
    """The window qualifies a budget; with no cap there is no funding to expire.

    Blocking here would invent a limit nobody set.
    """
    from shared.services.budget_guard import check_budgets
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, proj = await _seed_hierarchy(tenant, proj_budget=None)
    try:
        await _set_window(tenant, proj, start="2020-01-01", end="2020-12-31")
        _clear_caches()
        await check_budgets(tenant, proj)  # must not raise
    finally:
        await _cleanup(tenant)


# ── no defaults: an unset budget is no cap, at every level ───────────────────


@pytest.mark.asyncio
async def test_an_unset_unit_budget_bounds_nothing_below_it():
    """A business unit with no cap does not bound its projects.

    effective_cap used to answer DEFAULT_WORKSPACE_BUDGET_USD here, so an uncapped
    unit silently limited every project under it to $50 — a ceiling nobody chose,
    enforced against people who could not see where it came from.
    """
    from shared.services.budget_alloc import assert_project_fits
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, proj = await _seed_hierarchy(tenant, org_budget=None, ws_budget=None)
    try:
        async with get_db_session_for_tenant(tenant) as s:
            # Far past every old default; with no unit cap there is nothing to exceed.
            await assert_project_fits(s, tenant, ws, 100_000, on_create=True)
    finally:
        await _cleanup(tenant)


@pytest.mark.asyncio
async def test_uncapped_siblings_do_not_consume_the_parents_budget():
    """The committed total counts what was SET, not what a default imagined.

    Summing COALESCE(budget, default) meant two uncapped units "used up" $100 of an
    org cap they were never charged against.
    """
    from shared.services.budget_alloc import assert_workspace_fits
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, proj = await _seed_hierarchy(tenant, org_budget=100, ws_budget=None)
    try:
        async with get_db_session_for_tenant(tenant) as s:
            # The existing unit commits nothing, so the whole $100 is still free.
            await assert_workspace_fits(s, tenant, 100, on_create=True)
    finally:
        await _cleanup(tenant)


@pytest.mark.asyncio
async def test_an_explicit_cap_is_still_enforced_at_every_level():
    """Removing the defaults must not remove the limits somebody did set."""
    from fastapi import HTTPException
    from shared.services.budget_alloc import assert_project_fits, assert_workspace_fits
    tenant = str(uuid.uuid4())
    _clear_caches()
    ws, proj = await _seed_hierarchy(tenant, org_budget=100, ws_budget=60, proj_budget=50)
    try:
        async with get_db_session_for_tenant(tenant) as s:
            with pytest.raises(HTTPException) as exc:
                await assert_workspace_fits(s, tenant, 80, on_create=True)
            assert exc.value.status_code == 409
            with pytest.raises(HTTPException) as exc:
                await assert_project_fits(s, tenant, ws, 40, on_create=True)
            assert exc.value.status_code == 409
    finally:
        await _cleanup(tenant)
