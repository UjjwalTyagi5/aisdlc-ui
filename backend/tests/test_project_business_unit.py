"""A project's Business Unit survives the round trip.

Two bugs made a BU-Admin-created project show up as "Unassigned" on the Projects
screen, and neither was visible to the existing scope tests because both concern
WHICH unit a project belongs to rather than WHETHER the caller may see it:

  1. `ProjectOut` never serialized `workspaceId`. The ORM set it on create and the
     list query filtered on it, but it was absent from every response. The Zod
     counterpart is `.nullable().optional()`, so the missing field parsed cleanly to
     undefined and the UI grouped every project under "Unassigned" — a silent
     degradation with no error anywhere.

  2. `create_project` ignored the body's `workspaceId`. `ProjectCreateIn` did not
     declare the field, so Pydantic dropped it, and the project was attached to the
     X-Workspace-Id selector instead — or, when that header was absent, to the org's
     oldest workspace. The unit chosen in the create dialog was discarded.

The fixture therefore has TWO units. With one, a project attached to the wrong unit
is indistinguishable from one attached to the right unit.
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
    """One org, TWO units, one project already sitting in the second one."""
    org = str(_uuid.uuid4())
    bu_a, bu_b = str(_uuid.uuid4()), str(_uuid.uuid4())
    proj_in_b = str(_uuid.uuid4())
    other_org, other_bu = str(_uuid.uuid4()), str(_uuid.uuid4())

    async with get_db_session_superuser() as s:
        for oid, slug in ((org, f"bu-test-{org[:8]}"), (other_org, f"other-{other_org[:8]}")):
            await s.execute(text(
                "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'BU Test')"
            ), {"i": oid, "s": slug})
        # created_at ordering decides the "org's oldest workspace" fallback, so A is
        # inserted first: it is what the buggy path would have chosen.
        for wid, oid, slug, name in (
            (bu_a, org, "unit-a", "Unit A"),
            (bu_b, org, "unit-b", "Unit B"),
            (other_bu, other_org, "unit-x", "Unit X"),
        ):
            await s.execute(text(
                "INSERT INTO workspaces (id, organization_id, slug, display_name) "
                "VALUES (:i, :o, :s, :n)"
            ), {"i": wid, "o": oid, "s": slug, "n": name})

    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Sits In B')"
        ), {"i": proj_in_b, "w": bu_b, "t": org})

    yield {"org": org, "bu_a": bu_a, "bu_b": bu_b, "proj_in_b": proj_in_b,
           "other_bu": other_bu}

    async with get_db_session_for_tenant(org) as s:
        await s.execute(text("DELETE FROM role_bindings"))
        await s.execute(text("DELETE FROM projects"))
    async with get_db_session_superuser() as s:
        for oid in (org, other_org):
            await s.execute(text(
                "DELETE FROM workspaces WHERE organization_id = CAST(:t AS uuid)"
            ), {"t": oid})
            await s.execute(text(
                "DELETE FROM organizations WHERE id = CAST(:t AS uuid)"
            ), {"t": oid})


def _client() -> TestClient:
    return TestClient(process_api.app)


def _headers(user_id: str, org: str, selector: str, permissions: list[str]) -> dict:
    token = create_access_token(user_id=user_id, tenant_id=org, permissions=permissions)
    return {"Authorization": f"Bearer {token}", "X-Workspace-Id": selector}


@pytest.mark.asyncio
async def test_list_reports_the_projects_business_unit(two_units):
    """Bug 1: without this the UI has nothing to group by and shows 'Unassigned'."""
    t = two_units
    admin = f"admin-{_uuid.uuid4()}"
    await grant_role(admin, t["org"], "org_admin", tenant_id=t["org"], scope_kind="organization")

    c = _client()
    r = c.get("/projects", headers=_headers(admin, t["org"], t["bu_a"], ["admin:*"]))
    assert r.status_code == 200, r.text
    rows = {i["id"]: i for i in r.json()["items"]}
    assert t["proj_in_b"] in rows
    assert rows[t["proj_in_b"]]["workspaceId"] == t["bu_b"]


@pytest.mark.asyncio
async def test_org_admin_sees_projects_from_every_unit(two_units):
    """The list must not be pinned to the active-workspace selector.

    The selector here names Unit A; the only project lives in Unit B. Pinned to the
    selector this returns nothing, and the project 'vanishes' after being created.
    """
    t = two_units
    admin = f"admin-{_uuid.uuid4()}"
    await grant_role(admin, t["org"], "org_admin", tenant_id=t["org"], scope_kind="organization")

    c = _client()
    r = c.get("/projects", headers=_headers(admin, t["org"], t["bu_a"], ["admin:*"]))
    assert r.status_code == 200, r.text
    assert {i["id"] for i in r.json()["items"]} == {t["proj_in_b"]}


@pytest.mark.asyncio
async def test_create_honours_the_body_unit_over_the_header(two_units):
    """Bug 2: the chosen unit wins, even when the ambient selector says otherwise."""
    t = two_units
    admin = f"admin-{_uuid.uuid4()}"
    await grant_role(admin, t["org"], "org_admin", tenant_id=t["org"], scope_kind="organization")

    c = _client()
    # Selector says Unit A — and Unit A is also the org's oldest, so both buggy paths
    # would land here. The body says Unit B.
    r = c.post(
        "/projects",
        headers=_headers(admin, t["org"], t["bu_a"], ["admin:*", "workspace:manage"]),
        json={"name": "Chosen Unit B", "workspaceId": t["bu_b"], "monthlyBudgetUsd": 1000},
    )
    assert r.status_code == 201, r.text
    assert r.json()["workspaceId"] == t["bu_b"]


@pytest.mark.asyncio
async def test_create_rejects_a_unit_from_another_org(two_units):
    """A body-named unit is an explicit choice, so a foreign one is an error.

    Not a silent fallback to the org's first workspace: that is what turns a wrong id
    into a project quietly filed under the wrong unit.
    """
    t = two_units
    admin = f"admin-{_uuid.uuid4()}"
    await grant_role(admin, t["org"], "org_admin", tenant_id=t["org"], scope_kind="organization")

    c = _client()
    r = c.post(
        "/projects",
        headers=_headers(admin, t["org"], t["bu_a"], ["admin:*", "workspace:manage"]),
        json={"name": "Foreign Unit", "workspaceId": t["other_bu"]},
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_a_project_admin_can_create_a_project(two_units):
    """The permission that NAMES the act is the one the route asks for.

    `POST /projects` required `workspace:manage`, which only bu_admin holds, while
    `project:create` — held by both bu_admin and project_admin, and the permission the
    frontend's `canCreateProject` gates the "New project" button on — was enforced
    nowhere. A Project Admin was shown the button and got a 403 on submit.

    The permission list here is project_admin's real one, minus the parts irrelevant to
    creating: notably it does NOT include `workspace:manage`, which is the whole point.
    """
    t = two_units
    admin = f"projadmin-{_uuid.uuid4()}"
    await grant_role(admin, t["org"], "org_admin", tenant_id=t["org"], scope_kind="organization")

    c = _client()
    r = c.post(
        "/projects",
        headers=_headers(admin, t["org"], t["bu_a"], ["artifact:view", "project:create"]),
        json={"name": "Made By A Project Admin", "workspaceId": t["bu_b"], "monthlyBudgetUsd": 1000},
    )
    assert r.status_code == 201, r.text
    assert r.json()["workspaceId"] == t["bu_b"]


@pytest.mark.asyncio
async def test_creating_a_project_still_needs_a_permission(two_units):
    """The read floor is not enough — deny-by-default still holds."""
    t = two_units
    nobody = f"nobody-{_uuid.uuid4()}"
    await grant_role(nobody, t["org"], "org_admin", tenant_id=t["org"], scope_kind="organization")

    c = _client()
    r = c.post(
        "/projects",
        headers=_headers(nobody, t["org"], t["bu_a"], ["artifact:view"]),
        json={"name": "No Permission", "workspaceId": t["bu_b"]},
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_create_without_a_unit_is_refused(two_units):
    """There is no default workspace, so a create that names no unit is an error.

    The selector here names a perfectly valid unit and the org has an oldest workspace
    to fall back to — both of the things the old code would have used. Neither may
    stand in for a choice: a project filed under a unit nobody picked is exactly the
    "Default Workspace" group this rule exists to prevent.
    """
    t = two_units
    admin = f"admin-{_uuid.uuid4()}"
    await grant_role(admin, t["org"], "org_admin", tenant_id=t["org"], scope_kind="organization")

    c = _client()
    r = c.post(
        "/projects",
        headers=_headers(admin, t["org"], t["bu_a"], ["admin:*", "workspace:manage"]),
        json={"name": "No Unit Given"},
    )
    assert r.status_code == 422, r.text
