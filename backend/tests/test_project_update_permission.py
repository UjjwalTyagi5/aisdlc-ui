"""`project:update` is a real permission, and PATCH /projects/{id} enforces it.

Three things were tangled together:

  · The string was used as a `require_permission` argument by seven Agent Studio routes
    while being in NO catalogue and granted to NO role — so those routes answered only
    to `admin:*`, silently. They have since moved to `skill:*`.
  · The frontend passed it to `hasPermission()` on the project Settings tab and budget
    field, where the same emptiness meant a Project Admin and a Business Unit Admin saw
    no Settings tab at all.
  · `PATCH /projects/{id}` meanwhile demanded `workspace:manage`, which only bu_admin
    holds — so the Project Admin, who is made to choose a budget when creating the
    project, could not change the figure afterwards.

The string is now real, granted to bu_admin and project_admin, and enforced here.
Widening WHO may edit made WHICH project mandatory, which is the second half of these
tests.

See docs/rbac-audit-2026-08-17.md.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.authz.grant import grant_role
from shared.authz.permissions import ALL_PERMISSIONS, _ROLE_PERMISSIONS
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def two_units():
    org = str(_uuid.uuid4())
    unit_a, unit_b = str(_uuid.uuid4()), str(_uuid.uuid4())
    proj_a, proj_b = str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'PU Test')"
        ), {"i": org, "s": f"pu-{org[:8]}"})
        for wid, slug in ((unit_a, "unit-a"), (unit_b, "unit-b")):
            await s.execute(text(
                "INSERT INTO workspaces (id, organization_id, slug, display_name) "
                "VALUES (:i, :o, :s, :s)"
            ), {"i": wid, "o": org, "s": slug})
    async with get_db_session_for_tenant(org) as s:
        for pid, wid, name in ((proj_a, unit_a, "Gamma"), (proj_b, unit_b, "Delta")):
            await s.execute(text(
                "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
                "VALUES (:i, :w, :t, :n)"
            ), {"i": pid, "w": wid, "t": org, "n": name})
    yield {"org": org, "unit_a": unit_a, "unit_b": unit_b,
           "proj_a": proj_a, "proj_b": proj_b}


def _client() -> TestClient:
    return TestClient(process_api.app)


def _hdr(user_id: str, org: str, perms: list[str]) -> dict:
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, tenant_id=org, permissions=perms)
    }


def test_the_string_is_grantable_and_granted():
    """The emptiness that made every gate on it a no-op is what this asserts is gone."""
    assert "project:update" in ALL_PERMISSIONS
    holders = {r for r, p in _ROLE_PERMISSIONS.items() if "project:update" in p}
    assert holders == {"bu_admin", "project_admin"}


@pytest.mark.asyncio
async def test_a_project_admins_budget_edit_is_queued_for_approval(two_units):
    """Reaching the route is no longer the same as changing the figure.

    This test used to assert the budget came back as 250. It does not any more, and the
    change is deliberate rather than a regression: a Project Admin's settings edit is now
    a request their Business Unit Admin decides (projects.py::_queue_settings_change).

    The permission half of the original bug still holds and is still what this asserts
    first — `project:update`, not `workspace:manage`, so the call is authorised and
    answers 200. What changed is the effect: 200 means "filed", and the assertions below
    exist so that "filed" can never quietly become "silently dropped". The project keeps
    its old value AND a request exists naming the new one.
    """
    t = two_units
    user = f"pa-{_uuid.uuid4()}"
    await grant_role(user, t["proj_a"], "project_admin",
                     tenant_id=t["org"], scope_kind="project")

    r = _client().patch(
        f"/projects/{t['proj_a']}",
        headers=_hdr(user, t["org"], ["artifact:view", "project:update", "member:manage"]),
        json={"monthlyBudgetUsd": 250},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    # Not applied — the approver must not be asked about something already done.
    assert body["monthlyBudgetUsd"] is None
    assert body["pendingApproval"] is True
    assert body["pendingRequestId"]
    assert body["pendingApproverRole"] == "bu_admin"

    # And the figure survived into the request, rather than the edit evaporating.
    async with get_db_session_for_tenant(t["org"]) as s:
        row = (await s.execute(text(
            "SELECT type, status, payload FROM governance_requests WHERE id = :i"
        ), {"i": body["pendingRequestId"]})).first()
    assert row is not None, "the edit was accepted but no request was filed"
    assert row.type == "project_settings_change"
    assert row.status == "submitted"
    assert row.payload["changes"]["monthlyBudgetUsd"] == 250


@pytest.mark.asyncio
async def test_the_bu_admin_approving_it_applies_the_budget(two_units):
    """The other end of the loop, which nothing covered.

    A queue nobody drains is worse than no queue: the Project Admin is told the change
    was sent, the Business Unit Admin approves it, and if the effect never fires the
    figure is simply wrong with everyone believing it was agreed. This walks the whole
    path — edit, file, decide, apply — and asserts the number actually lands.
    """
    t = two_units
    pa = f"pa-{_uuid.uuid4()}"
    bu = f"bu-{_uuid.uuid4()}"
    await grant_role(pa, t["proj_a"], "project_admin",
                     tenant_id=t["org"], scope_kind="project")
    await grant_role(bu, t["unit_a"], "bu_admin",
                     tenant_id=t["org"], scope_kind="business_unit")

    c = _client()
    filed = c.patch(
        f"/projects/{t['proj_a']}",
        headers=_hdr(pa, t["org"], ["artifact:view", "project:update", "member:manage"]),
        json={"monthlyBudgetUsd": 250},
    )
    assert filed.status_code == 200, filed.text
    request_id = filed.json()["pendingRequestId"]

    decided = c.post(
        f"/governance-approvals/{request_id}/decide",
        headers=_hdr(bu, t["org"], ["artifact:view", "governance:decide"]),
        json={"decision": "approve"},
    )
    assert decided.status_code == 200, decided.text

    async with get_db_session_for_tenant(t["org"]) as s:
        budget = (await s.execute(text(
            "SELECT monthly_budget_usd FROM projects WHERE id = :i"
        ), {"i": t["proj_a"]})).scalar()
    assert budget is not None, "approved, but the change never reached the project"
    assert float(budget) == 250


@pytest.mark.asyncio
async def test_a_project_admin_cannot_edit_someone_elses_project(two_units):
    """The second half. `_get_or_404` scopes by tenant alone, so widening the permission
    without a project-scope check would have handed every Project Admin every project in
    the organisation — trading a usability bug for a real one."""
    t = two_units
    user = f"pa-{_uuid.uuid4()}"
    await grant_role(user, t["proj_a"], "project_admin",
                     tenant_id=t["org"], scope_kind="project")

    r = _client().patch(
        f"/projects/{t['proj_b']}",
        headers=_hdr(user, t["org"], ["artifact:view", "project:update", "member:manage"]),
        json={"monthlyBudgetUsd": 999},
    )
    # 404, not 403 — consistent with the sibling scope guards.
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_a_bu_admin_can_still_edit_projects_in_their_unit(two_units):
    """The role that could edit before must not lose the ability."""
    t = two_units
    user = f"bu-{_uuid.uuid4()}"
    await grant_role(user, t["unit_a"], "bu_admin",
                     tenant_id=t["org"], scope_kind="business_unit")
    hdr = _hdr(user, t["org"], ["artifact:view", "project:update", "workspace:manage"])
    c = _client()

    assert c.patch(f"/projects/{t['proj_a']}", headers=hdr,
                   json={"name": "Gamma renamed"}).status_code == 200
    assert c.patch(f"/projects/{t['proj_b']}", headers=hdr,
                   json={"name": "nope"}).status_code == 404


@pytest.mark.asyncio
async def test_a_developer_cannot_edit_the_project_they_work_on(two_units):
    """Being on a project is not running it. `developer` holds no project:update."""
    t = two_units
    user = f"dev-{_uuid.uuid4()}"
    await grant_role(user, t["proj_a"], "developer",
                     tenant_id=t["org"], scope_kind="project")

    r = _client().patch(
        f"/projects/{t['proj_a']}",
        headers=_hdr(user, t["org"], ["artifact:view", "run:create"]),
        json={"monthlyBudgetUsd": 1},
    )
    assert r.status_code == 403, r.text
