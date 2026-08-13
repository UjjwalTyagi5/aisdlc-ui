"""Projects and runs are scoped to the caller, not just to the tenant.

Both endpoints previously filtered by tenant alone — `runs.py` did not reference
role_bindings at all — and the Next.js tier narrowed the rows afterwards. Removing
that tier removed the enforcement, so these tests are the replacement: they exercise
the real app through HTTP, because the thing being asserted is what a route returns,
not what a helper computes.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.authz.grant import grant_role
from shared.db import get_db_session_for_tenant, get_db_session_superuser

# Same reason as test_can_perform: `grant_role` upserts a users row per subject, which
# the tree fixture's teardown does not remove.
pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def tree():
    """One org, one unit, two projects, one run in each."""
    org, bu = str(_uuid.uuid4()), str(_uuid.uuid4())
    proj_a, proj_b = str(_uuid.uuid4()), str(_uuid.uuid4())
    run_a, run_b = str(_uuid.uuid4()), str(_uuid.uuid4())

    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Scope Test')"
        ), {"i": org, "s": f"scope-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org})

    # projects and runs are FORCE RLS — the tenant GUC must be set to insert.
    async with get_db_session_for_tenant(org) as s:
        for pid, name in ((proj_a, "Project A"), (proj_b, "Project B")):
            await s.execute(text(
                "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
                "VALUES (:i, :w, :t, :n)"
            ), {"i": pid, "w": bu, "t": org, "n": name})
        for rid, pid in ((run_a, proj_a), (run_b, proj_b)):
            await s.execute(text(
                "INSERT INTO runs (id, project_id, tenant_id, stage, status) "
                "VALUES (:i, :p, :t, 'requirements', 'running')"
            ), {"i": rid, "p": pid, "t": org})

    yield {"org": org, "bu": bu, "proj_a": proj_a, "proj_b": proj_b,
           "run_a": run_a, "run_b": run_b}

    async with get_db_session_for_tenant(org) as s:
        await s.execute(text("DELETE FROM role_bindings"))
        await s.execute(text("DELETE FROM runs"))
        await s.execute(text("DELETE FROM projects"))
    async with get_db_session_superuser() as s:
        await s.execute(text("DELETE FROM workspaces WHERE organization_id = CAST(:t AS uuid)"), {"t": org})
        await s.execute(text("DELETE FROM organizations WHERE id = CAST(:t AS uuid)"), {"t": org})


def _client() -> TestClient:
    # No context manager: the lifespan would start the Redis-backed workers.
    return TestClient(process_api.app)


def _headers(user_id: str, org: str, bu: str, permissions: list[str]) -> dict:
    token = create_access_token(user_id=user_id, tenant_id=org, permissions=permissions)
    # The projects list filters by the active workspace selector as well as by scope.
    return {"Authorization": f"Bearer {token}", "X-Workspace-Id": bu}


@pytest.mark.asyncio
async def test_project_list_shows_only_the_callers_projects(tree):
    t = tree
    user = f"dev-{_uuid.uuid4()}"
    await grant_role(user, t["proj_a"], "developer", tenant_id=t["org"], scope_kind="project")

    c = _client()
    r = c.get("/projects", headers=_headers(user, t["org"], t["bu"], ["artifact:view"]))
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {item["id"] for item in body["items"]}
    assert ids == {t["proj_a"]}, ids
    # The total must describe the filtered set, or the count contradicts the page.
    assert body["pagination"]["total"] == 1


@pytest.mark.asyncio
async def test_project_detail_of_an_unreachable_project_is_404(tree):
    t = tree
    user = f"dev-{_uuid.uuid4()}"
    await grant_role(user, t["proj_a"], "developer", tenant_id=t["org"], scope_kind="project")
    h = _headers(user, t["org"], t["bu"], ["artifact:view"])

    c = _client()
    assert c.get(f"/projects/{t['proj_a']}", headers=h).status_code == 200
    # 404, not 403 — a project you cannot see must not be confirmed to exist.
    assert c.get(f"/projects/{t['proj_b']}", headers=h).status_code == 404


@pytest.mark.asyncio
async def test_org_binding_sees_every_project(tree):
    t = tree
    admin = f"admin-{_uuid.uuid4()}"
    await grant_role(admin, t["org"], "org_admin", tenant_id=t["org"], scope_kind="organization")

    c = _client()
    r = c.get("/projects", headers=_headers(admin, t["org"], t["bu"], ["admin:*"]))
    assert r.status_code == 200, r.text
    assert {i["id"] for i in r.json()["items"]} == {t["proj_a"], t["proj_b"]}


@pytest.mark.asyncio
async def test_unit_binding_sees_every_project_in_that_unit(tree):
    t = tree
    user = f"bu-{_uuid.uuid4()}"
    await grant_role(user, t["bu"], "bu_admin", tenant_id=t["org"], scope_kind="business_unit")

    c = _client()
    r = c.get("/projects", headers=_headers(user, t["org"], t["bu"], ["artifact:view"]))
    assert {i["id"] for i in r.json()["items"]} == {t["proj_a"], t["proj_b"]}


@pytest.mark.asyncio
async def test_no_bindings_sees_nothing(tree):
    """The empty-list case: [] must not be treated as 'no filter'."""
    t = tree
    c = _client()
    r = c.get(
        "/projects",
        headers=_headers(f"nobody-{_uuid.uuid4()}", t["org"], t["bu"], ["artifact:view"]),
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"] == []
    assert r.json()["pagination"]["total"] == 0


@pytest.mark.asyncio
async def test_run_list_shows_only_runs_in_reachable_projects(tree):
    t = tree
    user = f"dev-{_uuid.uuid4()}"
    await grant_role(user, t["proj_a"], "developer", tenant_id=t["org"], scope_kind="project")

    c = _client()
    r = c.get("/runs", headers=_headers(user, t["org"], t["bu"], ["artifact:view"]))
    assert r.status_code == 200, r.text
    assert {i["id"] for i in r.json()["items"]} == {t["run_a"]}


@pytest.mark.asyncio
async def test_run_detail_outside_scope_is_404(tree):
    """_get_run_or_404 is the chokepoint for sixteen routes — guarding it guards them."""
    t = tree
    user = f"dev-{_uuid.uuid4()}"
    await grant_role(user, t["proj_a"], "developer", tenant_id=t["org"], scope_kind="project")
    h = _headers(user, t["org"], t["bu"], ["artifact:view"])

    c = _client()
    assert c.get(f"/runs/{t['run_a']}", headers=h).status_code == 200
    assert c.get(f"/runs/{t['run_b']}", headers=h).status_code == 404
    # A sibling route through the same chokepoint must answer identically.
    assert c.get(f"/runs/{t['run_b']}/steps", headers=h).status_code == 404


@pytest.mark.asyncio
async def test_run_list_for_an_unbound_user_is_empty(tree):
    t = tree
    c = _client()
    r = c.get(
        "/runs",
        headers=_headers(f"nobody-{_uuid.uuid4()}", t["org"], t["bu"], ["artifact:view"]),
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"] == []
