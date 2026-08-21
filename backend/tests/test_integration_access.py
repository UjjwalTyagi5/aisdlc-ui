"""Integration grants: the middle level of the cascade.

  onboarded  the org has a connection      connectors / mcp_servers
  GRANTED    a unit may use it             integration_grants          <- under test
  used       a project wired it            projects.connectors / .mcp_servers

The authorisation split is what these tests pin. Granting is the Organization
Admin's, because a unit that could grant itself an integration has no grant; revoking
at PROJECT level is either admin tier's, because an admin taking something away has
to be able to stop one team without punishing the rest of the unit.
"""
import json
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
async def org():
    org_id = str(_uuid.uuid4())
    payments, lending = str(_uuid.uuid4()), str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Grants Test')"
        ), {"i": org_id, "s": f"grt-{org_id[:8]}"})
        for wid, slug, name in ((payments, "payments", "Payments"), (lending, "lending", "Lending")):
            await s.execute(text(
                "INSERT INTO workspaces (id, organization_id, slug, display_name) "
                "VALUES (:i, :o, :s, :n)"
            ), {"i": wid, "o": org_id, "s": slug, "n": name})
    async with get_db_session_for_tenant(org_id) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name, provider_kind, connectors) "
            "VALUES (CAST(:i AS uuid), CAST(:w AS uuid), CAST(:t AS uuid), 'Core ledger', 'github', "
            "        CAST(:c AS jsonb))"
        ), {"i": project, "w": payments, "t": org_id,
            "c": json.dumps({"development": ["jira"], "deployment": ["jira"]})})
    yield {"org": org_id, "payments": payments, "lending": lending, "project": project}


async def _user(org: dict, handle: str) -> str:
    uid = f"{handle}-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO users (id, email, password_hash, tenant_id, active) "
            "VALUES (:i, :e, 'x', :t, true)"
        ), {"i": uid, "e": f"{handle}@abcbank.com", "t": org["org"]})
    return uid


def _headers(uid: str, org_id: str, perms: list[str]) -> dict:
    return {"Authorization": "Bearer " + create_access_token(
        user_id=uid, tenant_id=org_id, permissions=perms)}


def _row(body: list, kind: str, target: str) -> dict:
    return next(r for r in body if r["kind"] == kind and r["id"] == target)


@pytest.mark.asyncio
async def test_nothing_is_granted_until_somebody_grants_it(org):
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])

    body = c.get("/integrations/access", headers=headers).json()
    jira = _row(body, "connector", "jira")
    # Every visible unit is listed so the UI can OFFER the grant — you cannot give a
    # unit something it is not on the list for.
    assert {u["name"] for u in jira["units"]} == {"Payments", "Lending"}
    assert all(u["via"] == "none" for u in jira["units"])
    assert jira["grantedUnitCount"] == 0
    # Not onboarded either: no connection exists behind it yet.
    assert jira["onboarded"] is False


@pytest.mark.asyncio
async def test_granting_then_revoking_a_unit(org):
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])

    r = c.post("/integrations/access", headers=headers,
               params={"kind": "connector", "id": "jira", "workspaceId": org["payments"]})
    assert r.status_code == 200, r.text

    jira = _row(c.get("/integrations/access", headers=headers).json(), "connector", "jira")
    assert jira["grantedUnitCount"] == 1
    assert [u["via"] for u in jira["units"] if u["name"] == "Payments"] == ["granted"]
    assert [u["via"] for u in jira["units"] if u["name"] == "Lending"] == ["none"]

    # Granting twice is the same state, not an error — the grant IS the key.
    assert c.post("/integrations/access", headers=headers,
                  params={"kind": "connector", "id": "jira",
                          "workspaceId": org["payments"]}).status_code == 200

    gone = c.request("DELETE", "/integrations/access", headers=headers,
                     params={"kind": "connector", "id": "jira", "level": "unit",
                             "workspaceId": org["payments"]})
    assert gone.json() == {"ok": True, "changed": True}
    # Idempotent: revoking what is already gone satisfies the intent.
    again = c.request("DELETE", "/integrations/access", headers=headers,
                      params={"kind": "connector", "id": "jira", "level": "unit",
                              "workspaceId": org["payments"]})
    assert again.json() == {"ok": True, "changed": False}


@pytest.mark.asyncio
async def test_a_unit_admin_cannot_grant_themselves_an_integration(org):
    """The rule the whole table exists for. `connector:manage` is a BU Admin's for
    their own unit's connections — a unit that can grant itself has no grant."""
    c = TestClient(process_api.app)
    bua = await _user(org, "farah")
    await grant_role(bua, org["payments"], "bu_admin",
                     tenant_id=org["org"], scope_kind="business_unit")
    headers = _headers(bua, org["org"], ["connector:view", "connector:manage"])

    r = c.post("/integrations/access", headers=headers,
               params={"kind": "connector", "id": "jira", "workspaceId": org["payments"]})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "forbidden"


@pytest.mark.asyncio
async def test_a_grant_names_a_unit_not_a_project(org):
    """Granting is the organisation's reach decision; a project switching it on is
    the project's own wiring."""
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    r = c.post("/integrations/access", headers=_headers(admin, org["org"], ["admin:*"]),
               params={"kind": "connector", "id": "jira", "projectId": org["project"]})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "unit_level_only"


@pytest.mark.asyncio
async def test_project_usage_is_reported_and_revocable(org):
    """The third level: what a project actually wired, and stopping one team without
    taking the grant away from the unit."""
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])
    c.post("/integrations/access", headers=headers,
           params={"kind": "connector", "id": "jira", "workspaceId": org["payments"]})

    jira = _row(c.get("/integrations/access", headers=headers).json(), "connector", "jira")
    payments = next(u for u in jira["units"] if u["name"] == "Payments")
    assert [p["name"] for p in payments["projects"]] == ["Core ledger"]
    assert sorted(payments["projects"][0]["stages"]) == ["deployment", "development"]
    assert jira["projectCount"] == 1

    revoked = c.request("DELETE", "/integrations/access", headers=headers,
                        params={"kind": "connector", "id": "jira", "level": "project",
                                "projectId": org["project"]})
    assert revoked.json() == {"ok": True, "changed": True}

    after = _row(c.get("/integrations/access", headers=headers).json(), "connector", "jira")
    assert after["projectCount"] == 0
    # The UNIT keeps the grant — stopping one team is not taking it away from all.
    assert after["grantedUnitCount"] == 1


@pytest.mark.asyncio
async def test_a_bounded_viewer_sees_only_their_own_units(org):
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    c.post("/integrations/access", headers=_headers(admin, org["org"], ["admin:*"]),
           params={"kind": "connector", "id": "jira", "workspaceId": org["lending"]})

    bua = await _user(org, "farah")
    await grant_role(bua, org["payments"], "bu_admin",
                     tenant_id=org["org"], scope_kind="business_unit")
    body = c.get("/integrations/access",
                 headers=_headers(bua, org["org"], ["connector:view"])).json()
    jira = _row(body, "connector", "jira")
    # Lending is not theirs to see, so neither is the fact that Lending holds Jira.
    assert {u["name"] for u in jira["units"]} == {"Payments"}
    assert jira["grantedUnitCount"] == 0


@pytest.mark.asyncio
async def test_connector_grants_shape_matches_the_ui_contract(org):
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])

    put = c.put("/connectors/grants", headers=headers,
                params={"workspaceId": org["payments"]},
                json={"kinds": ["jira", "github"]})
    assert put.status_code == 200, put.text
    by_kind = {g["kind"]: g["businessUnitIds"] for g in put.json()}
    assert by_kind["jira"] == [org["payments"]]
    assert by_kind["github"] == [org["payments"]]

    # Replacing the unit's set removes what is not in it.
    c.put("/connectors/grants", headers=headers,
          params={"workspaceId": org["payments"]}, json={"kinds": ["jira"]})
    by_kind = {g["kind"]: g["businessUnitIds"] for g in c.get("/connectors/grants", headers=headers).json()}
    assert "github" not in by_kind
    assert by_kind["jira"] == [org["payments"]]


# The four tests that stood here pinned an invariant that no longer exists.
#
# They asserted that PUT /connectors/grants preserved each surviving grant's ACCESS
# LEVEL across its DELETE-then-INSERT, so re-saving a policy could not silently strip
# write access. Migration 0024 removed the level from `integration_grants` entirely —
# read vs write is decided per project stage now — so a grant row is just its own
# existence and there is nothing left for a replace to lose.
#
# What replaced them: tests/test_per_stage_tool_access.py, which pins the resolution
# that took over, including the two things that must still fail closed (an ungranted
# unit, and a stage that never wired the connector).
