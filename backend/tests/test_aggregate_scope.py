"""Aggregating endpoints compute their totals from the caller's own units.

`read_scope.py` states the rule this file enforces: *a count discloses as much as a row*.
Telling a Business Unit Admin that the organisation spent £40,000 last month is a fact
about units they cannot open. Four surfaces did not follow it — the audit trail, the cost
breakdown, the budget hub, and traces — while `spend.py`, reading the same money, narrowed
correctly all along.

The permissions involved say WHAT, never WHOSE: `audit:view` is held by bu_admin and
security_engineer, `cost:view` by those plus project_admin, `trace:view` by project_admin
and security_engineer. None of them means "the whole organisation".

See finding 4 in docs/rbac-audit-2026-08-17.md.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.authz.grant import grant_role
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def two_units():
    """Two units with a project each, and an audit event in each.

    The point of every test here is the unit the caller is NOT in.
    """
    org = str(_uuid.uuid4())
    unit_a, unit_b = str(_uuid.uuid4()), str(_uuid.uuid4())
    proj_a, proj_b = str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name, monthly_budget_usd) "
            "VALUES (:i, :s, 'Agg Test', 5000)"
        ), {"i": org, "s": f"agg-{org[:8]}"})
        for wid, slug, budget in ((unit_a, "unit-a", 1000), (unit_b, "unit-b", 2000)):
            await s.execute(text(
                "INSERT INTO workspaces (id, organization_id, slug, display_name, monthly_budget_usd) "
                "VALUES (:i, :o, :s, :s, :b)"
            ), {"i": wid, "o": org, "s": slug, "b": budget})
    async with get_db_session_for_tenant(org) as s:
        for pid, wid, name in ((proj_a, unit_a, "Alpha"), (proj_b, unit_b, "Beta")):
            await s.execute(text(
                "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
                "VALUES (:i, :w, :t, :n)"
            ), {"i": pid, "w": wid, "t": org, "n": name})
        # One resource event per unit, plus an RBAC event carrying the OTHER payload
        # shape (scope_kind/scope_id) so the filter is exercised on both.
        for wid, actor in ((unit_a, "in-scope"), (unit_b, "out-of-scope")):
            await s.execute(text(
                "INSERT INTO audit_events (id, tenant_id, actor_id, event_type, "
                "  resource_type, resource_id, payload) "
                "VALUES (gen_random_uuid(), CAST(:t AS uuid), :a, 'thing.happened', "
                "  'thing', 'r', CAST(:p AS jsonb))"
            ), {"t": org, "a": actor, "p": f'{{"workspace_id": "{wid}"}}'})
            await s.execute(text(
                "INSERT INTO audit_events (id, tenant_id, actor_id, event_type, "
                "  resource_type, resource_id, payload) "
                "VALUES (gen_random_uuid(), CAST(:t AS uuid), :a, 'rbac.role_granted', "
                "  'business_unit', :w, CAST(:p AS jsonb))"
            ), {"t": org, "a": f"rbac-{actor}", "w": wid,
                "p": f'{{"scope_kind": "business_unit", "scope_id": "{wid}"}}'})
    yield {"org": org, "unit_a": unit_a, "unit_b": unit_b,
           "proj_a": proj_a, "proj_b": proj_b}


def _client() -> TestClient:
    return TestClient(process_api.app)


def _hdr(user_id: str, org: str, perms: list[str]) -> dict:
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, tenant_id=org, permissions=perms)
    }


async def _bu_admin_of(unit: str, org: str) -> str:
    user = f"bu-{_uuid.uuid4()}"
    await grant_role(user, unit, "bu_admin", tenant_id=org, scope_kind="business_unit")
    return user


# ── the audit trail ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_unit_admin_sees_only_their_own_units_audit_events(two_units):
    """Reachable by simply omitting the workspace filter, which is caller-supplied and
    therefore was never a control."""
    t = two_units
    user = await _bu_admin_of(t["unit_a"], t["org"])

    r = _client().get("/audit", headers=_hdr(user, t["org"], ["artifact:view", "audit:view"]))
    assert r.status_code == 200, r.text
    actors = {i["actor"]["id"] for i in r.json()["items"]}
    assert "out-of-scope" not in actors
    assert "in-scope" in actors


@pytest.mark.asyncio
async def test_rbac_events_for_their_own_unit_stay_visible(two_units):
    """The two payload shapes. RBAC events carry `scope_kind`/`scope_id`, not
    `workspace_id`; filtering on the latter alone would hide a unit admin's own grants
    and revocations — the events they are most accountable for."""
    t = two_units
    user = await _bu_admin_of(t["unit_a"], t["org"])

    r = _client().get("/audit", headers=_hdr(user, t["org"], ["artifact:view", "audit:view"]))
    actors = {i["actor"]["id"] for i in r.json()["items"]}
    assert "rbac-in-scope" in actors
    assert "rbac-out-of-scope" not in actors


@pytest.mark.asyncio
async def test_asking_for_a_foreign_unit_is_refused_not_silently_rescoped(two_units):
    """Answering a question about someone else's unit with the viewer's own events is a
    wrong answer presented as a right one."""
    t = two_units
    user = await _bu_admin_of(t["unit_a"], t["org"])

    r = _client().get(
        f"/audit?workspace_id={t['unit_b']}",
        headers=_hdr(user, t["org"], ["artifact:view", "audit:view"]),
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_an_org_admin_still_sees_the_whole_trail(two_units):
    t = two_units
    user = f"oa-{_uuid.uuid4()}"
    await grant_role(user, t["org"], "org_admin", tenant_id=t["org"],
                     scope_kind="organization")

    r = _client().get("/audit", headers=_hdr(user, t["org"], ["admin:*"]))
    assert r.status_code == 200, r.text
    actors = {i["actor"]["id"] for i in r.json()["items"]}
    assert {"in-scope", "out-of-scope"} <= actors


# ── the budget hub ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_budget_hub_shows_only_the_units_the_caller_administers(two_units):
    """The sharpest disclosure in the finding: every sibling unit's budget AND spend,
    presented as a hub."""
    t = two_units
    user = await _bu_admin_of(t["unit_a"], t["org"])

    r = _client().get("/cost/budgets", headers=_hdr(user, t["org"], ["artifact:view", "cost:view"]))
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {w["id"] for w in body["workspaces"]}
    assert ids == {t["unit_a"]}
    assert {p["id"] for p in body["projects"]} == {t["proj_a"]}


@pytest.mark.asyncio
async def test_the_org_rollup_is_computed_from_the_visible_units(two_units):
    """`allocatedUsd` summed every unit's budget, so the org total leaked the size of
    units the caller cannot open even once the rows were filtered."""
    t = two_units
    scoped_user = await _bu_admin_of(t["unit_a"], t["org"])
    admin = f"oa-{_uuid.uuid4()}"
    await grant_role(admin, t["org"], "org_admin", tenant_id=t["org"],
                     scope_kind="organization")

    c = _client()
    scoped = c.get("/cost/budgets", headers=_hdr(scoped_user, t["org"], ["artifact:view", "cost:view"])).json()
    whole = c.get("/cost/budgets", headers=_hdr(admin, t["org"], ["admin:*"])).json()

    # Unit A alone is 1000; both units are 3000.
    assert scoped["org"]["allocatedUsd"] == 1000
    assert whole["org"]["allocatedUsd"] == 3000
    # The org's own CAP stays visible — it is the ceiling they allocate under.
    assert scoped["org"]["monthlyBudgetUsd"] == whole["org"]["monthlyBudgetUsd"]


@pytest.mark.asyncio
async def test_someone_with_no_units_sees_nothing_rather_than_everything(two_units):
    """`[]` and `None` mean opposite things in read_scope, and treating the empty list as
    "no filter" shows a brand-new account the whole organisation."""
    t = two_units
    user = f"nobody-{_uuid.uuid4()}"

    r = _client().get("/cost/budgets", headers=_hdr(user, t["org"], ["artifact:view", "cost:view"]))
    assert r.status_code == 200, r.text
    assert r.json()["workspaces"] == []
    assert r.json()["projects"] == []


@pytest.mark.asyncio
async def test_the_cost_breakdown_refuses_a_foreign_unit(two_units):
    t = two_units
    user = await _bu_admin_of(t["unit_a"], t["org"])

    r = _client().get(
        f"/cost?workspace={t['unit_b']}", headers=_hdr(user, t["org"], ["artifact:view", "cost:view"])
    )
    assert r.status_code == 404, r.text


# ── traces ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_project_summary_for_a_foreign_project_is_refused(two_units):
    """Zeroes are themselves a fact about the project, and indistinguishable from
    'no spend yet' — so this refuses rather than answering."""
    t = two_units
    user = f"pa-{_uuid.uuid4()}"
    await grant_role(user, t["proj_a"], "project_admin", tenant_id=t["org"],
                     scope_kind="project")

    c = _client()
    hdr = _hdr(user, t["org"], ["artifact:view", "trace:view"])
    assert c.get(f"/traces/project-summary?project_id={t['proj_b']}",
                 headers=hdr).status_code == 404
    # Their own project is answered normally (zeroed while Langfuse is off).
    assert c.get(f"/traces/project-summary?project_id={t['proj_a']}",
                 headers=hdr).status_code == 200
