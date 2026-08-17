"""Agent-access overrides, project credentials, cross-BU loans, and role edits.

Each of these was returning empty or 501 because its table did not exist. The tests
concentrate on the rules that are easy to get subtly wrong once the storage is there:
who may write, whose credential is whose, and which side of a loan may end it.
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
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Scoped Test')"
        ), {"i": org_id, "s": f"scp-{org_id[:8]}"})
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
            "c": json.dumps({"development": ["jira"]})})
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


async def _grant_integration(org: dict, kind: str, target: str, workspace: str) -> None:
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO integration_grants (tenant_id, kind, target_ref, workspace_id) "
            "VALUES (CAST(:t AS uuid), :k, :r, CAST(:w AS uuid)) ON CONFLICT DO NOTHING"
        ), {"t": org["org"], "k": kind, "r": target, "w": workspace})


# ── agent access overrides ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_overrides_means_the_built_in_matrix(org):
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    r = c.get(f"/projects/{org['project']}/agent-access-overrides",
              headers=_headers(admin, org["org"], ["admin:*"]))
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_setting_an_override_is_an_upsert_and_deleting_resets_it(org):
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])
    url = f"/projects/{org['project']}/agent-access-overrides"

    first = c.put(url, headers=headers,
                  json={"role": "qa", "phase": "security", "involvement": "use"})
    assert first.status_code == 200, first.text
    assert first.json()["involvement"] == "use"

    # Same pair again is ONE answer, not two rows — ambiguity here would make
    # "what does a QA reach" depend on which row the query read.
    second = c.put(url, headers=headers,
                   json={"role": "qa", "phase": "security", "involvement": "primary"})
    assert second.json()["involvement"] == "primary"
    assert len(c.get(url, headers=headers).json()) == 1

    assert c.request("DELETE", url, headers=headers,
                     params={"role": "qa", "phase": "security"}).status_code == 204
    assert c.get(url, headers=headers).json() == []


@pytest.mark.asyncio
async def test_an_invalid_involvement_is_refused(org):
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    r = c.put(f"/projects/{org['project']}/agent-access-overrides",
              headers=_headers(admin, org["org"], ["admin:*"]),
              json={"role": "qa", "phase": "security", "involvement": "god"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_a_project_admin_cannot_override_a_project_they_do_not_run(org):
    c = TestClient(process_api.app)
    pa = await _user(org, "sofia")
    # Bound to a DIFFERENT project's unit, holding the permission but not the project.
    await grant_role(pa, org["lending"], "bu_admin",
                     tenant_id=org["org"], scope_kind="business_unit")
    r = c.put(f"/projects/{org['project']}/agent-access-overrides",
              headers=_headers(pa, org["org"], ["artifact:view", "member:manage"]),
              json={"role": "qa", "phase": "security", "involvement": "use"})
    assert r.status_code == 404


# ── project integrations ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_project_may_only_use_what_its_unit_was_granted(org):
    """The cascade. Configuring a credential for something the unit never got is a
    403, not a silent no-op."""
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])

    assert c.get(f"/projects/{org['project']}/integrations", headers=headers).json() == []

    r = c.put(f"/projects/{org['project']}/integrations", headers=headers,
              json={"kind": "connector", "targetId": "jira", "account": "acme"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "not_granted"

    await _grant_integration(org, "connector", "jira", org["payments"])
    listed = c.get(f"/projects/{org['project']}/integrations", headers=headers).json()
    assert [i["targetId"] for i in listed] == ["jira"]
    # The project's own wiring is reported alongside the permission.
    assert listed[0]["stages"] == ["development"]


@pytest.mark.asyncio
async def test_two_people_configuring_the_same_tool_do_not_overwrite_each_other(org):
    """Keyed on the OWNER. Keyed on the project alone, the second contributor to
    configure Jira silently replaced the first and neither could tell."""
    c = TestClient(process_api.app)
    await _grant_integration(org, "connector", "jira", org["payments"])
    alice, bob = await _user(org, "alice"), await _user(org, "bob")
    url = f"/projects/{org['project']}/integrations"

    c.put(url, headers=_headers(alice, org["org"], ["admin:*"]),
          json={"kind": "connector", "targetId": "jira", "account": "alice-bot"})
    c.put(url, headers=_headers(bob, org["org"], ["admin:*"]),
          json={"kind": "connector", "targetId": "jira", "account": "bob-bot"})

    creds = c.get(url, headers=_headers(alice, org["org"], ["admin:*"])).json()[0]["credentials"]
    assert {x["ownerId"] for x in creds} == {alice, bob}
    assert {x["account"] for x in creds} == {"alice-bot", "bob-bot"}


@pytest.mark.asyncio
async def test_a_secret_is_never_stored_or_returned(org):
    """There is nowhere proper to put one yet, so it is read and discarded rather
    than written into a column that would end up in every backup."""
    c = TestClient(process_api.app)
    await _grant_integration(org, "connector", "jira", org["payments"])
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])

    r = c.put(f"/projects/{org['project']}/integrations", headers=headers,
              json={"kind": "connector", "targetId": "jira", "secret": "hunter2"})
    assert "hunter2" not in r.text
    assert r.json()["credentials"][0]["hasSecret"] is False

    async with get_db_session_for_tenant(org["org"]) as s:
        row = (await s.execute(
            text("SELECT secret_ref FROM project_integration_credentials")
        )).first()
    assert row.secret_ref is None


# ── cross-BU loans ───────────────────────────────────────────────────────────

async def _lend(org: dict, user_id: str) -> None:
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO cross_bu_grants "
            "  (id, tenant_id, user_id, parent_workspace_id, project_id, role, approved_by) "
            "VALUES (CAST(:i AS uuid), CAST(:t AS uuid), :u, CAST(:pw AS uuid), "
            "        CAST(:p AS uuid), 'developer', 'marcus')"
        ), {"i": str(_uuid.uuid4()), "t": org["org"], "u": user_id,
            "pw": org["lending"], "p": org["project"]})


@pytest.mark.asyncio
async def test_a_loan_is_visible_from_both_sides(org):
    """One fact seen from two sides — who of mine is elsewhere, whose people are here."""
    c = TestClient(process_api.app)
    borrowed = await _user(org, "marcus")
    await _lend(org, borrowed)

    lender = await _user(org, "lendadmin")
    await grant_role(lender, org["lending"], "bu_admin",
                     tenant_id=org["org"], scope_kind="business_unit")
    borrower = await _user(org, "payadmin")
    await grant_role(borrower, org["payments"], "bu_admin",
                     tenant_id=org["org"], scope_kind="business_unit")

    from_lender = c.get("/admin/cross-bu-grants",
                        headers=_headers(lender, org["org"], ["artifact:view", "member:manage"])).json()
    assert len(from_lender) == 1 and from_lender[0]["lentByYou"] is True

    from_borrower = c.get("/admin/cross-bu-grants",
                          headers=_headers(borrower, org["org"], ["artifact:view", "member:manage"])).json()
    assert len(from_borrower) == 1 and from_borrower[0]["lentByYou"] is False
    assert from_borrower[0]["parentWorkspaceName"] == "Lending"
    assert from_borrower[0]["targetWorkspaceName"] == "Payments"


@pytest.mark.asyncio
async def test_only_the_lending_unit_can_end_a_loan(org):
    """The borrower can take them off the project like any other member; ending the
    LOAN is the lender's, because it is their person and their headcount."""
    c = TestClient(process_api.app)
    borrowed = await _user(org, "marcus")
    await _lend(org, borrowed)
    await grant_role(borrowed, org["project"], "developer",
                     tenant_id=org["org"], scope_kind="project")

    borrower = await _user(org, "payadmin")
    await grant_role(borrower, org["payments"], "bu_admin",
                     tenant_id=org["org"], scope_kind="business_unit")
    denied = c.request("DELETE", "/admin/cross-bu-grants",
                       headers=_headers(borrower, org["org"], ["artifact:view", "member:manage"]),
                       json={"identityId": borrowed, "projectId": org["project"]})
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "not_the_lender"

    lender = await _user(org, "lendadmin")
    await grant_role(lender, org["lending"], "bu_admin",
                     tenant_id=org["org"], scope_kind="business_unit")
    ok = c.request("DELETE", "/admin/cross-bu-grants",
                   headers=_headers(lender, org["org"], ["artifact:view", "member:manage"]),
                   json={"identityId": borrowed, "projectId": org["project"]})
    assert ok.json() == {"ok": True, "changed": True}

    # The seat goes with the loan — otherwise they keep working on a project their
    # own unit has taken them off.
    async with get_db_session_for_tenant(org["org"]) as s:
        left = (await s.execute(
            text("SELECT 1 FROM role_bindings WHERE user_id = :u AND scope_kind = 'project'"),
            {"u": borrowed},
        )).first()
    assert left is None


# ── custom role edits ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_custom_role_can_be_renamed_and_repermissioned(org):
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    # A real org_admin BINDING, not just the JWT claim: creating a custom role checks
    # that the caller holds every permission they are granting, and that check reads
    # DB-resolved permissions rather than the token — you cannot grant what you do
    # not actually have.
    await grant_role(admin, org["org"], "org_admin",
                     tenant_id=org["org"], scope_kind="organization")
    headers = _headers(admin, org["org"], ["admin:*"])

    created = c.post("/admin/custom-roles", headers=headers,
                     json={"name": "Junior Dev", "permissions": ["artifact:view"]})
    assert created.status_code == 201, created.text
    role_id = created.json()["id"]

    patched = c.patch(f"/admin/custom-roles/{role_id}", headers=headers,
                      json={"name": "Junior Developer",
                            "permissions": ["artifact:view", "run:view"]})
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Junior Developer"
    assert sorted(patched.json()["permissions"]) == ["artifact:view", "run:view"]

    listed = {r["id"]: r for r in c.get("/admin/custom-roles", headers=headers).json()}
    assert listed[role_id]["name"] == "Junior Developer"


@pytest.mark.asyncio
async def test_a_unit_admin_cannot_edit_the_org_wide_role(org):
    """It is assignable in every unit, so changing it changes what people outside
    their authority may do."""
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    await grant_role(admin, org["org"], "org_admin",
                     tenant_id=org["org"], scope_kind="organization")
    created = c.post("/admin/custom-roles", headers=_headers(admin, org["org"], ["admin:*"]),
                     json={"name": "Org Wide", "permissions": ["artifact:view"]})
    role_id = created.json()["id"]

    bua = await _user(org, "farah")
    await grant_role(bua, org["payments"], "bu_admin",
                     tenant_id=org["org"], scope_kind="business_unit")
    r = c.patch(f"/admin/custom-roles/{role_id}",
                headers=_headers(bua, org["org"], ["artifact:view", "role:manage"]),
                json={"name": "Hijacked", "permissions": ["admin:*"]})
    assert r.status_code == 403
