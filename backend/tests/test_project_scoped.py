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
        # No `access` column since migration 0024 — a grant is reach only, and what
        # these tests mean by granting is exactly that.
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
              json={"kind": "connector", "targetId": "jira", "label": "Acme bot", "account": "acme"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "not_granted"

    await _grant_integration(org, "connector", "jira", org["payments"])
    listed = c.get(f"/projects/{org['project']}/integrations", headers=headers).json()
    assert [i["id"] for i in listed] == ["jira"]
    # The project's own wiring is reported alongside the permission.
    assert listed[0]["stages"] == ["development"]


@pytest.mark.asyncio
async def test_two_people_configuring_the_same_tool_do_not_overwrite_each_other(org):
    """Keyed on the OWNER. Keyed on the project alone, the second contributor to
    configure Jira silently replaced the first and neither could tell. And each
    only ever sees their own — "You never see theirs" per the project Integrations
    page's own docstring — never a shared list of every owner's credential."""
    c = TestClient(process_api.app)
    await _grant_integration(org, "connector", "jira", org["payments"])
    alice, bob = await _user(org, "alice"), await _user(org, "bob")
    url = f"/projects/{org['project']}/integrations"

    c.put(url, headers=_headers(alice, org["org"], ["admin:*"]),
          json={"kind": "connector", "targetId": "jira", "label": "Alice's Jira", "account": "alice-bot"})
    c.put(url, headers=_headers(bob, org["org"], ["admin:*"]),
          json={"kind": "connector", "targetId": "jira", "label": "Bob's Jira", "account": "bob-bot"})

    alice_view = c.get(url, headers=_headers(alice, org["org"], ["admin:*"])).json()[0]
    bob_view = c.get(url, headers=_headers(bob, org["org"], ["admin:*"])).json()[0]
    assert alice_view["credential"]["ownerId"] == alice
    assert alice_view["credential"]["account"] == "alice-bot"
    assert bob_view["credential"]["ownerId"] == bob
    assert bob_view["credential"]["account"] == "bob-bot"


@pytest.mark.asyncio
async def test_a_secret_is_stored_but_never_returned_in_plaintext(org):
    """The raw value never round-trips in a response or sits in plaintext in the
    row — but it IS stored now (via secret_store, referenced by secret_ref) and
    genuinely retrievable, which is what makes it usable by a real connector call."""
    c = TestClient(process_api.app)
    await _grant_integration(org, "connector", "jira", org["payments"])
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])

    r = c.put(f"/projects/{org['project']}/integrations", headers=headers,
              json={"kind": "connector", "targetId": "jira", "label": "Test bot", "secret": "hunter2"})
    assert "hunter2" not in r.text
    assert r.json()["hasSecret"] is True

    async with get_db_session_for_tenant(org["org"]) as s:
        row = (await s.execute(
            text("SELECT secret_ref FROM project_integration_credentials")
        )).first()
    assert row.secret_ref is not None
    assert row.secret_ref != "hunter2"  # a reference, never the value itself

    from shared.authz.project_credential import resolve_project_secret
    resolved = await resolve_project_secret(
        tenant_id=org["org"], project_id=org["project"], owner_id=admin,
        kind="connector", target_id="jira",
    )
    assert resolved == "hunter2"


@pytest.mark.asyncio
async def test_the_instance_is_the_projects_and_the_identity_is_the_members(org):
    """The two halves of a connection are stored apart because they are governed apart.

    base_url says WHERE the project authenticates and lives on the project;
    account + secret say WHO is authenticating and live on the member's own
    credential. Resolving them returns one record, so a connector still sees a
    whole credential.
    """
    c = TestClient(process_api.app)
    await _grant_integration(org, "connector", "jira", org["payments"])
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])

    r = c.put(f"/projects/{org['project']}/integrations/instance", headers=headers,
              json={"kind": "connector", "targetId": "jira",
                    "baseUrl": "https://ana-team.atlassian.net"})
    assert r.status_code == 200, r.text
    assert r.json()["baseUrl"] == "https://ana-team.atlassian.net"

    r = c.put(f"/projects/{org['project']}/integrations", headers=headers,
              json={"kind": "connector", "targetId": "jira", "label": "Test bot",
                    "account": "ana@abcbank.com", "secret": "hunter2"})
    assert r.status_code == 200, r.text
    assert "hunter2" not in r.text  # still write-only

    from shared.authz.project_credential import resolve_project_credential
    creds = await resolve_project_credential(
        tenant_id=org["org"], project_id=org["project"], owner_id=admin,
        kind="connector", target_id="jira",
    )
    assert creds is not None
    assert creds.base_url == "https://ana-team.atlassian.net"   # from the project
    assert creds.account == "ana@abcbank.com"                    # from the member
    assert creds.token == "hunter2"

    listed = c.get(f"/projects/{org['project']}/integrations", headers=headers).json()
    jira = next(i for i in listed if i["id"] == "jira")
    assert jira["baseUrl"] == "https://ana-team.atlassian.net"
    assert jira["canManageInstance"] is True
    # The URL is NOT part of the credential any more.
    assert "baseUrl" not in jira["credential"]


@pytest.mark.asyncio
async def test_a_contributor_cannot_repoint_the_projects_integration(org):
    """THE GOVERNANCE RULE. A member may say who they are; only someone who runs
    the project may say where that identity is sent.

    Left on the credential, any contributor could aim the project's Jira at a
    host of their own choosing and the platform would authenticate and read
    there — which is why base_url moved off it (migration 0032).
    """
    c = TestClient(process_api.app)
    await _grant_integration(org, "connector", "jira", org["payments"])
    admin = await _user(org, "orgadmin")
    dev = await _user(org, "developer")
    dev_headers = _headers(dev, org["org"], ["connector:view", "artifact:view"])

    c.put(f"/projects/{org['project']}/integrations/instance",
          headers=_headers(admin, org["org"], ["admin:*"]),
          json={"kind": "connector", "targetId": "jira",
                "baseUrl": "https://sanctioned.atlassian.net"})

    # The developer may save their OWN credential …
    r = c.put(f"/projects/{org['project']}/integrations", headers=dev_headers,
              json={"kind": "connector", "targetId": "jira", "label": "Dev bot",
                    "account": "dev@abcbank.com", "secret": "devtoken"})
    assert r.status_code == 200, r.text

    # … but must not be able to move the project onto a host of their choosing.
    #
    # 404, not 403, and deliberately so: assert_can_administer_project refuses
    # without confirming the project exists to someone who does not run it
    # (shared/authz/project_scope.py). What matters here is that the write did
    # not happen — asserted below, because a refusal that still mutated would
    # pass a status-code check and fail the point of it.
    r = c.put(f"/projects/{org['project']}/integrations/instance", headers=dev_headers,
              json={"kind": "connector", "targetId": "jira",
                    "baseUrl": "https://evil.atlassian.net"})
    assert r.status_code in (403, 404), f"a contributor repointed the integration: {r.text}"

    # And the connector still resolves the sanctioned host for THAT developer.
    from shared.authz.project_credential import resolve_project_credential
    creds = await resolve_project_credential(
        tenant_id=org["org"], project_id=org["project"], owner_id=dev,
        kind="connector", target_id="jira",
    )
    assert creds is not None
    assert creds.base_url == "https://sanctioned.atlassian.net"
    assert creds.token == "devtoken"

    # The list tells them they may not change it, so the UI can say so.
    listed = c.get(f"/projects/{org['project']}/integrations", headers=dev_headers).json()
    jira = next(i for i in listed if i["id"] == "jira")
    assert jira["baseUrl"] == "https://sanctioned.atlassian.net"
    assert jira["canManageInstance"] is False


@pytest.mark.asyncio
async def test_resolve_project_secret_still_returns_a_bare_token(org):
    """The narrow wrapper keeps working for callers with nowhere to put a URL.

    shared/services/mcp_registry.py folds this straight into an Authorization
    header; widening its return type would have broken that silently.
    """
    c = TestClient(process_api.app)
    await _grant_integration(org, "connector", "jira", org["payments"])
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])
    c.put(f"/projects/{org['project']}/integrations", headers=headers,
          json={"kind": "connector", "targetId": "jira", "label": "Test bot",
                "baseUrl": "https://ana-team.atlassian.net", "secret": "hunter2"})

    from shared.authz.project_credential import resolve_project_secret
    assert await resolve_project_secret(
        tenant_id=org["org"], project_id=org["project"], owner_id=admin,
        kind="connector", target_id="jira",
    ) == "hunter2"


@pytest.mark.asyncio
async def test_a_connector_authenticates_at_the_projects_own_url(org, monkeypatch):
    """THE POINT OF ALL THIS: with no org-wide configuration of any kind, a
    connector still resolves a working URL — the one the member typed.

    There are no tenant-wide tiers to empty any more — the connector reads only this
    tenant's own secrets — so the only place the URL below can have come from is the
    project credential.
    """
    c = TestClient(process_api.app)
    await _grant_integration(org, "connector", "sonarqube", org["payments"])
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])
    c.put(f"/projects/{org['project']}/integrations/instance", headers=headers,
          json={"kind": "connector", "targetId": "sonarqube",
                "baseUrl": "https://sonar.ledger.internal"})
    c.put(f"/projects/{org['project']}/integrations", headers=headers,
          json={"kind": "connector", "targetId": "sonarqube", "label": "Ledger scanner",
                "secret": "squ_abc123"})

    from config.connector_factory import get_connector_for_session

    connector = await get_connector_for_session(
        kind="sonarqube", tenant_id=org["org"], project_id=org["project"],
        owner_id=admin, unrestricted=True,
    )
    auth = await connector.auth_adapter(org["org"])
    assert auth["sonarqube_url"] == "https://sonar.ledger.internal"
    assert auth["token"] == "squ_abc123"


@pytest.mark.asyncio
async def test_a_credential_without_a_url_still_works(org, monkeypatch):
    """A row written before base_url existed carries only a token, and must keep
    authenticating against the tenant-wide URL rather than against nothing.

    The tenant-wide URL is now the tenant's own `sonarqube-url` Key Vault secret — it
    used to be the SONARQUBE_URL env var, one value shared by every tenant."""
    import config.connectors.sonarqube as _sq

    async def _tenant_wide_url(ref, tenant_id=None):
        if ref == "sonarqube-url" and tenant_id:
            return "https://sonar.tenant-wide.internal"
        return None

    monkeypatch.setattr(_sq._keyvault, "load_secret", _tenant_wide_url)

    c = TestClient(process_api.app)
    await _grant_integration(org, "connector", "sonarqube", org["payments"])
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])
    c.put(f"/projects/{org['project']}/integrations", headers=headers,
          json={"kind": "connector", "targetId": "sonarqube", "label": "Ledger scanner",
                "secret": "squ_abc123"})

    from config.connector_factory import get_connector_for_session

    connector = await get_connector_for_session(
        kind="sonarqube", tenant_id=org["org"], project_id=org["project"],
        owner_id=admin, unrestricted=True,
    )
    auth = await connector.auth_adapter(org["org"])
    assert auth["sonarqube_url"] == "https://sonar.tenant-wide.internal"
    assert auth["token"] == "squ_abc123"


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


@pytest.mark.asyncio
async def test_a_contributor_cannot_aim_the_probe_at_their_own_host(org):
    """The test endpoint must not become the hole the instance endpoint closed.

    A contributor may send any baseUrl they like; the server has to ignore it and
    probe the project's pinned instance instead, or "Test connection" would be a
    way to make the platform authenticate against a host of their choosing.
    """
    c = TestClient(process_api.app)
    await _grant_integration(org, "connector", "sonarqube", org["payments"])
    admin = await _user(org, "orgadmin")
    dev = await _user(org, "developer")

    c.put(f"/projects/{org['project']}/integrations/instance",
          headers=_headers(admin, org["org"], ["admin:*"]),
          json={"kind": "connector", "targetId": "sonarqube",
                "baseUrl": "https://sanctioned.example.com"})

    from shared.routers import project_scoped as ps

    seen: list = []

    async def _spy(self, tenant_id: str = ""):  # noqa: ANN001
        seen.append(getattr(self, "_credential_override_base_url", None))
        raise RuntimeError("stop before any network call")

    import config.connectors.sonarqube as sq
    original = sq.SonarQubeConnector.health_check

    async def _fake_health(self):  # noqa: ANN001
        seen.append(getattr(self, "_credential_override_base_url", None))
        raise RuntimeError("stop before any network call")

    sq.SonarQubeConnector.health_check = _fake_health
    try:
        c.post(f"/projects/{org['project']}/integrations/test-connection",
               headers=_headers(dev, org["org"], ["connector:view", "artifact:view"]),
               json={"kind": "connector", "targetId": "sonarqube", "secret": "x",
                     "baseUrl": "https://attacker.example.com"})
    finally:
        sq.SonarQubeConnector.health_check = original

    assert seen, "the probe never ran"
    assert seen[-1] == "https://sanctioned.example.com", (
        f"a contributor redirected the probe to {seen[-1]!r}"
    )


# ── project settings changes route to the BU Admin ───────────────────────────


async def _bind_project_admin(org: dict, user_id: str) -> None:
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO role_bindings (id, tenant_id, user_id, role_name, scope_kind, scope_id) "
            "VALUES (CAST(:i AS uuid), CAST(:t AS uuid), :u, 'project_admin', 'project', :p)"
        ), {"i": str(_uuid.uuid4()), "t": org["org"], "u": user_id, "p": org["project"]})
        await s.commit()


async def _bind_bu_admin(org: dict, user_id: str) -> None:
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO role_bindings (id, tenant_id, user_id, role_name, scope_kind, scope_id) "
            "VALUES (CAST(:i AS uuid), CAST(:t AS uuid), :u, 'bu_admin', 'business_unit', :w)"
        ), {"i": str(_uuid.uuid4()), "t": org["org"], "u": user_id, "w": org["payments"]})
        await s.commit()


@pytest.mark.asyncio
async def test_a_project_admins_settings_edit_becomes_a_request(org):
    """The edit is QUEUED, not applied — and it is addressed to the BU Admin.

    Applying it and marking it pending would be the worst of both: the change live,
    and the approver asked to sign off something already done.
    """
    c = TestClient(process_api.app)
    pa = await _user(org, "projadmin")
    await _bind_project_admin(org, pa)
    headers = _headers(pa, org["org"], ["project:update", "artifact:view"])

    r = c.patch(f"/projects/{org['project']}", headers=headers,
                json={"name": "Renamed by the project admin"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pendingApproval"] is True
    assert body["pendingApproverRole"] == "bu_admin"
    # The project is UNCHANGED in the same response.
    assert body["name"] != "Renamed by the project admin"

    async with get_db_session_for_tenant(org["org"]) as s:
        name = (await s.execute(
            text("SELECT display_name FROM projects WHERE id = CAST(:p AS uuid)"),
            {"p": org["project"]},
        )).scalar()
    assert name != "Renamed by the project admin"


@pytest.mark.asyncio
async def test_a_bu_admin_edit_applies_directly(org):
    """The approver of that request does not need to raise one against themselves."""
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])

    r = c.patch(f"/projects/{org['project']}", headers=headers,
                json={"name": "Renamed by the org admin"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("pendingApproval") in (False, None)
    assert body["name"] == "Renamed by the org admin"


@pytest.mark.asyncio
async def test_approving_the_request_applies_the_settings(org):
    """Approval is a gate, not a note: the values land on the project."""
    from shared.services import governance_requests as gov
    c = TestClient(process_api.app)
    pa = await _user(org, "projadmin")
    bua = await _user(org, "buadmin")
    await _bind_project_admin(org, pa)
    # decide()'s gate-integrity check (sub-project B) now verifies the decider's own
    # role_bindings row covers the request's scope, not just their role name — bua needs
    # a real bu_admin binding on this project's business unit to be able to approve.
    await _bind_bu_admin(org, bua)

    r = c.patch(f"/projects/{org['project']}",
                headers=_headers(pa, org["org"], ["project:update", "artifact:view"]),
                json={"name": "Approved name", "monthlyBudgetUsd": 77})
    req_id = r.json()["pendingRequestId"]

    async with get_db_session_for_tenant(org["org"]) as s:
        await gov.decide(s, request_id=req_id, decider_id=bua, decider_name="Bua",
                         decider_role="bu_admin", decision="approve")

    async with get_db_session_for_tenant(org["org"]) as s:
        row = (await s.execute(
            text("SELECT display_name, monthly_budget_usd FROM projects "
                 "WHERE id = CAST(:p AS uuid)"),
            {"p": org["project"]},
        )).first()
    assert row.display_name == "Approved name"
    assert float(row.monthly_budget_usd) == 77.0


@pytest.mark.asyncio
async def test_an_unsent_field_is_not_blanked(org):
    """exclude_unset, not `is not None`.

    Without it every field the client omitted arrives as None, and renaming a
    project would read as a request to clear its budget and unwire every connector.
    """
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])
    c.patch(f"/projects/{org['project']}", headers=headers, json={"monthlyBudgetUsd": 42})
    c.patch(f"/projects/{org['project']}", headers=headers, json={"name": "Only the name"})

    async with get_db_session_for_tenant(org["org"]) as s:
        row = (await s.execute(
            text("SELECT display_name, monthly_budget_usd FROM projects "
                 "WHERE id = CAST(:p AS uuid)"),
            {"p": org["project"]},
        )).first()
    assert row.display_name == "Only the name"
    assert float(row.monthly_budget_usd) == 42.0


# ── a Project Admin can ask for more budget on their own project ─────────────


@pytest.mark.asyncio
async def test_project_budget_increase_request_reaches_the_bu_admin_and_applies(org):
    """The exact bug sub-project A's Task 1 parked: _apply_budget_increase's project_id
    branch (effects.py) has been dead code since it shipped — nothing has ever called
    create_request with request_type="budget_increase" and a real project_id. This
    proves the new endpoint makes that branch genuinely reachable end to end."""
    pa = await _user(org, "projadmin")
    bua = await _user(org, "buadmin")
    await _bind_project_admin(org, pa)
    await _bind_bu_admin(org, bua)

    # A sibling project in the SAME workspace, seeded with its own budget. The
    # whole point of the project-scoped branch is that it targets the row named
    # in the request, not something workspace-wide — so this decoy has to come
    # back untouched, or the endpoint is silently doing a workspace-wide update.
    decoy = str(_uuid.uuid4())
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name, provider_kind, "
            "monthly_budget_usd) VALUES (CAST(:i AS uuid), CAST(:w AS uuid), CAST(:t AS uuid), "
            "'Decoy project', 'github', 250)"
        ), {"i": decoy, "w": org["payments"], "t": org["org"]})
        await s.commit()

    c = TestClient(process_api.app)
    r = c.post(
        f"/projects/{org['project']}/budget-increase-request",
        headers=_headers(pa, org["org"], ["cost:view", "artifact:view"]),
        json={"requestedAmountUsd": 500, "reason": "Ran out mid-sprint."},
    )
    assert r.status_code == 201, r.text
    req_id = r.json()["id"]
    # Tier-routed (budget_increase is absent from routing.TYPE_ROUTED): a Project
    # Admin's raise climbs to their BU Admin, exactly as the cost page's own copy
    # already promises ("it escalates one tier at a time").
    assert r.json()["currentApproverRole"] == "bu_admin"

    from shared.services import governance_requests as gov
    async with get_db_session_for_tenant(org["org"]) as s:
        await gov.decide(s, request_id=req_id, decider_id=bua, decider_name="Bua",
                         decider_role="bu_admin", decision="approve")

    async with get_db_session_for_tenant(org["org"]) as s:
        row = (await s.execute(
            text("SELECT monthly_budget_usd FROM projects WHERE id = CAST(:p AS uuid)"),
            {"p": org["project"]},
        )).first()
        decoy_row = (await s.execute(
            text("SELECT monthly_budget_usd FROM projects WHERE id = CAST(:p AS uuid)"),
            {"p": decoy},
        )).first()
    assert float(row.monthly_budget_usd) == 500.0
    # THE ROW THAT MUST NOT MOVE. If _apply_budget_increase ever regressed to the
    # old workspace-wide UPDATE, this would silently jump to 500 right alongside
    # the target project — this assertion is what tells the difference.
    assert float(decoy_row.monthly_budget_usd) == 250.0


# ── budget window is captured at project creation (migration 0035) ───────────


@pytest.mark.asyncio
async def test_the_budget_window_survives_project_creation(org):
    """THE BUG THIS CLOSES. The create dialog has sent these two dates for a long
    time and ProjectCreateIn had no field for them, so Pydantic dropped them
    silently — typed into a form, submitted, gone."""
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])

    r = c.post("/projects", headers=headers, json={
        "name": "Funded phase", "workspaceId": org["payments"],
        "monthlyBudgetUsd": 20,
        "budgetStartDate": "2026-01-01", "budgetEndDate": "2026-03-31",
    })
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["budgetStartDate"] == "2026-01-01"
    assert body["budgetEndDate"] == "2026-03-31"

    async with get_db_session_for_tenant(org["org"]) as s:
        row = (await s.execute(
            text("SELECT budget_start_date, budget_end_date FROM projects "
                 "WHERE id = CAST(:p AS uuid)"),
            {"p": body["id"]},
        )).first()
    assert row.budget_start_date.isoformat() == "2026-01-01"
    assert row.budget_end_date.isoformat() == "2026-03-31"


@pytest.mark.asyncio
async def test_a_window_that_ends_before_it_starts_is_refused(org):
    """It could never be active, so the project could never run — 422 naming the
    field beats a project nobody can use and no obvious reason why."""
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    r = c.post("/projects", headers=_headers(admin, org["org"], ["admin:*"]), json={
        "name": "Backwards window", "workspaceId": org["payments"],
        "budgetStartDate": "2026-06-01", "budgetEndDate": "2026-01-01",
    })
    assert r.status_code == 422, r.text
