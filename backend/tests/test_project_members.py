"""A project's roster: who is on it, who may change it, and who is a guest.

The write guard is the point. `member:manage` says a caller may manage members
SOMEWHERE — a Business Unit Admin and a Project Admin both hold it — so without a
per-project check a Project Admin could staff any project in the tenant by passing its
id. That is the same hole `assert_can_write_workspace` closes one level up.
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
async def org():
    """Two units and two projects — a roster boundary needs a sibling to be visible."""
    org_id = str(_uuid.uuid4())
    payments, lending = str(_uuid.uuid4()), str(_uuid.uuid4())
    proj_a, proj_b = str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Roster Test')"
        ), {"i": org_id, "s": f"ros-{org_id[:8]}"})
        for wid, slug, name in ((payments, "payments", "Payments"), (lending, "lending", "Lending")):
            await s.execute(text(
                "INSERT INTO workspaces (id, organization_id, slug, display_name) "
                "VALUES (:i, :o, :s, :n)"
            ), {"i": wid, "o": org_id, "s": slug, "n": name})
    async with get_db_session_for_tenant(org_id) as s:
        for pid, wid, name in ((proj_a, payments, "Core ledger"), (proj_b, lending, "Mobile onboarding")):
            await s.execute(text(
                "INSERT INTO projects (id, workspace_id, tenant_id, display_name, provider_kind) "
                "VALUES (CAST(:i AS uuid), CAST(:w AS uuid), CAST(:t AS uuid), :n, 'github')"
            ), {"i": pid, "w": wid, "t": org_id, "n": name})
    yield {"org": org_id, "payments": payments, "lending": lending,
           "proj_a": proj_a, "proj_b": proj_b}


async def _user(org: dict, handle: str) -> str:
    user_id = f"{handle}-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO users (id, email, password_hash, tenant_id, active) "
            "VALUES (:i, :e, 'x', :t, true)"
        ), {"i": user_id, "e": f"{handle}@abcbank.com", "t": org["org"]})
    return user_id


def _headers(user_id: str, org_id: str, permissions: list[str]) -> dict:
    return {"Authorization": "Bearer " + create_access_token(
        user_id=user_id, tenant_id=org_id, permissions=permissions
    )}


@pytest.mark.asyncio
async def test_a_project_admin_staffs_their_own_project(org):
    c = TestClient(process_api.app)
    pa = await _user(org, "ana")
    dev = await _user(org, "diego")
    await grant_role(pa, org["proj_a"], "project_admin",
                     tenant_id=org["org"], scope_kind="project")

    headers = _headers(pa, org["org"], ["artifact:view", "member:manage"])
    r = c.post(f"/projects/{org['proj_a']}/members", headers=headers,
               json={"email": f"diego@abcbank.com", "roleName": "developer"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "developer"
    assert body["identity"]["id"] == dev
    assert body["identity"]["displayName"] == "Diego"
    # Same unit as the project, so the roster stays quiet about it.
    assert body["homeBusinessUnitName"] is None

    listed = c.get(f"/projects/{org['proj_a']}/members", headers=headers).json()
    assert {m["identity"]["id"] for m in listed} == {pa, dev}


@pytest.mark.asyncio
async def test_a_project_admin_cannot_staff_a_sibling_project(org):
    """The hole this guard exists to close: member:manage says SOMEWHERE, not WHICH."""
    c = TestClient(process_api.app)
    pa = await _user(org, "ana")
    await _user(org, "diego")
    await grant_role(pa, org["proj_a"], "project_admin",
                     tenant_id=org["org"], scope_kind="project")

    headers = _headers(pa, org["org"], ["artifact:view", "member:manage"])
    r = c.post(f"/projects/{org['proj_b']}/members", headers=headers,
               json={"email": "diego@abcbank.com", "roleName": "developer"})
    # 404, not 403 — a project they cannot reach is not confirmed to exist.
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_a_unit_admin_staffs_projects_in_their_unit(org):
    c = TestClient(process_api.app)
    bua = await _user(org, "farah")
    await _user(org, "diego")
    await grant_role(bua, org["payments"], "bu_admin",
                     tenant_id=org["org"], scope_kind="business_unit")

    headers = _headers(bua, org["org"], ["artifact:view", "member:manage"])
    assert c.post(f"/projects/{org['proj_a']}/members", headers=headers,
                  json={"email": "diego@abcbank.com", "roleName": "qa"}).status_code == 201
    # ...but not in the unit next door.
    assert c.post(f"/projects/{org['proj_b']}/members", headers=headers,
                  json={"email": "diego@abcbank.com", "roleName": "qa"}).status_code == 404


@pytest.mark.asyncio
async def test_someone_from_another_unit_is_named_as_a_guest(org):
    """The roster stays quiet about the unit everyone shares and speaks up about the
    one person who is not from it — whose headcount they are, and whose admin to go
    back to, is what a Project Admin needs and cannot otherwise see."""
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    guest = await _user(org, "marcus")
    await grant_role(guest, org["lending"], "developer",
                     tenant_id=org["org"], scope_kind="business_unit")

    headers = _headers(admin, org["org"], ["admin:*"])
    r = c.post(f"/projects/{org['proj_a']}/members", headers=headers,
               json={"email": "marcus@abcbank.com", "roleName": "developer"})
    assert r.status_code == 201, r.text
    assert r.json()["homeBusinessUnitName"] == "Lending"


@pytest.mark.asyncio
async def test_adding_an_unknown_email_does_not_create_an_account(org):
    """Admitting a person is an Organization Admin act. Onboarding here would put
    account creation behind member:manage, which a Project Admin holds."""
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])

    r = c.post(f"/projects/{org['proj_a']}/members", headers=headers,
               json={"email": "nobody@abcbank.com", "roleName": "developer"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "no_such_person"

    async with get_db_session_superuser() as s:
        found = (await s.execute(
            text("SELECT 1 FROM users WHERE email = 'nobody@abcbank.com'")
        )).first()
    assert found is None


@pytest.mark.asyncio
async def test_extra_agents_are_additive_and_editable(org):
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    await _user(org, "ingrid")
    headers = _headers(admin, org["org"], ["admin:*"])

    created = c.post(f"/projects/{org['proj_a']}/members", headers=headers,
                     json={"email": "ingrid@abcbank.com", "roleName": "qa",
                           "extraAgents": ["security"]}).json()
    assert created["extraAgents"] == ["security"]

    patched = c.patch(
        f"/projects/{org['proj_a']}/members/{created['membershipId']}",
        headers=headers, json={"extraAgents": ["security", "deployment"]},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["extraAgents"] == ["security", "deployment"]
    # The role is untouched by an extraAgents-only patch.
    assert patched.json()["role"] == "qa"


@pytest.mark.asyncio
async def test_a_membership_id_from_another_project_cannot_be_edited(org):
    """Guarded on scope_id as well as id — otherwise pairing a foreign membership id
    with a project you DO run would edit it."""
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    await _user(org, "diego")
    headers = _headers(admin, org["org"], ["admin:*"])

    elsewhere = c.post(f"/projects/{org['proj_b']}/members", headers=headers,
                       json={"email": "diego@abcbank.com", "roleName": "developer"}).json()

    r = c.patch(f"/projects/{org['proj_a']}/members/{elsewhere['membershipId']}",
                headers=headers, json={"roleName": "qa"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_removing_someone_already_gone_is_still_success(org):
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    await _user(org, "diego")
    headers = _headers(admin, org["org"], ["admin:*"])

    created = c.post(f"/projects/{org['proj_a']}/members", headers=headers,
                     json={"email": "diego@abcbank.com", "roleName": "developer"}).json()
    assert c.delete(f"/projects/{org['proj_a']}/members/{created['membershipId']}",
                    headers=headers).status_code == 204
    # Idempotent: the caller's intent is satisfied either way, and a 404 here would
    # read as "wrong project".
    assert c.delete(f"/projects/{org['proj_a']}/members/{created['membershipId']}",
                    headers=headers).status_code == 204
    assert c.get(f"/projects/{org['proj_a']}/members", headers=headers).json() == []
