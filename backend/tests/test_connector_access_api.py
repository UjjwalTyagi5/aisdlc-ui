"""The two doors into a connector grant, and the ceiling both respect.

There are exactly two ways access gets written: the direct endpoints, and approving
a `connector_access` governance request. A ceiling enforced at one and not the other
is not a ceiling — approving would simply be the way round it. These tests assert the
same refusal through both.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.authz.connector_grants import effective_access
from shared.db import get_db_session_for_tenant, get_db_session_superuser
from shared.governance.effects import EffectNotAvailable, apply_on_approve

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def tree():
    org, unit = str(_uuid.uuid4()), str(_uuid.uuid4())
    proj = str(_uuid.uuid4())
    admin = f"orgadmin-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'API Test')"
        ), {"i": org, "s": "capi-" + org[:8]})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'payments', 'Payments')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Ledger')"
        ), {"i": proj, "w": unit, "t": org})
    from shared.authz.grant import grant_role
    await grant_role(admin, org, "org_admin", tenant_id=org, scope_kind="organization")
    yield {"org": org, "unit": unit, "project": proj, "admin": admin}


def _client():
    return TestClient(process_api.app)


def _hdr(uid, org, perms):
    return {"Authorization": "Bearer " + create_access_token(
        user_id=uid, tenant_id=org, permissions=perms)}


def _admin(t):
    return _hdr(t["admin"], t["org"], ["admin:*"])


async def _effective(org, project, ref="jira"):
    async with get_db_session_for_tenant(org) as s:
        return await effective_access(s, tenant_id=org, project_id=project, target_ref=ref)


# ── the direct door ──────────────────────────────────────────────────────────

def test_a_grant_defaults_to_read(tree):
    """Least privilege by default: an Org Admin who wants write must say so."""
    c = _client()
    r = c.post("/integrations/access", headers=_admin(tree),
               params={"kind": "connector", "id": "jira", "workspaceId": tree["unit"]})
    assert r.status_code == 200, r.text
    assert r.json()["access"] == "read"


def test_a_bad_access_level_is_refused_not_coerced(tree):
    """A caller who sent a level meant something by it. Substituting the default
    would hand them a narrower grant than they think they made."""
    c = _client()
    r = c.post("/integrations/access", headers=_admin(tree),
               params={"kind": "connector", "id": "jira",
                       "workspaceId": tree["unit"], "access": "admin"})
    assert r.status_code == 422
    assert r.json().get("detail", {}).get("code") == "bad_access_level"


def test_regranting_changes_the_level(tree):
    """ON CONFLICT DO NOTHING made changing your mind a silent no-op that looked
    like it had worked."""
    c = _client()
    base = {"kind": "connector", "id": "jira", "workspaceId": tree["unit"]}
    c.post("/integrations/access", headers=_admin(tree), params={**base, "access": "read"})
    r = c.post("/integrations/access", headers=_admin(tree),
               params={**base, "access": "read_write"})
    assert r.json()["access"] == "read_write"


@pytest.mark.asyncio
async def test_a_project_cannot_be_given_more_than_its_unit(tree):
    """THE ESCALATION REFUSAL, through the API. Refused rather than narrowed —
    somebody who asked for write and quietly got read would believe they had write."""
    c = _client()
    c.post("/integrations/access", headers=_admin(tree),
           params={"kind": "connector", "id": "jira",
                   "workspaceId": tree["unit"], "access": "read"})

    r = c.put(f"/projects/{tree['project']}/integrations/access", headers=_admin(tree),
              json={"kind": "connector", "targetId": "jira", "access": "read_write"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "exceeds_grant"
    # And nothing was written.
    assert await _effective(tree["org"], tree["project"]) == "read"


@pytest.mark.asyncio
async def test_a_project_may_be_narrowed_and_restored(tree):
    c = _client()
    c.post("/integrations/access", headers=_admin(tree),
           params={"kind": "connector", "id": "jira",
                   "workspaceId": tree["unit"], "access": "read_write"})

    r = c.put(f"/projects/{tree['project']}/integrations/access", headers=_admin(tree),
              json={"kind": "connector", "targetId": "jira", "access": "read"})
    assert r.status_code == 200, r.text
    assert await _effective(tree["org"], tree["project"]) == "read"

    # Clearing the narrowing restores inheritance — it is not a revoke.
    r = c.request("DELETE", f"/projects/{tree['project']}/integrations/access",
                  headers=_admin(tree),
                  params={"kind": "connector", "targetId": "jira"})
    assert r.status_code == 200, r.text
    assert await _effective(tree["org"], tree["project"]) == "read_write"


def test_narrowing_an_ungranted_connector_is_refused(tree):
    c = _client()
    r = c.put(f"/projects/{tree['project']}/integrations/access", headers=_admin(tree),
              json={"kind": "connector", "targetId": "slack", "access": "read"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "not_granted"


@pytest.mark.asyncio
async def test_listing_shows_unit_and_effective_together(tree):
    c = _client()
    c.post("/integrations/access", headers=_admin(tree),
           params={"kind": "connector", "id": "jira",
                   "workspaceId": tree["unit"], "access": "read_write"})
    c.put(f"/projects/{tree['project']}/integrations/access", headers=_admin(tree),
          json={"kind": "connector", "targetId": "jira", "access": "read"})

    rows = c.get(f"/projects/{tree['project']}/integrations/access",
                 headers=_admin(tree)).json()
    row = next(r for r in rows if r["targetId"] == "jira")
    assert row["unitAccess"] == "read_write"
    assert row["projectAccess"] == "read"
    assert row["effectiveAccess"] == "read"
    assert row["inherited"] is False


# ── the approval door ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approving_a_unit_request_actually_grants(tree):
    """While this type recorded agreement and changed nothing, an approved request
    granted no access — an agent hit a denial holding an approval that said otherwise."""
    request = {
        "type": "connector_access",
        "tenantId": tree["org"],
        "workspaceId": tree["unit"],
        "projectId": None,
        "currentApproverRole": "org_admin",
        "decidedBy": "Org Admin",
        "payload": {"targetId": "jira", "kind": "connector",
                    "access": "read_write", "scope": "unit"},
    }
    async with get_db_session_for_tenant(tree["org"]) as s:
        note = await apply_on_approve(s, request)
    assert "read and write" in note
    assert await _effective(tree["org"], tree["project"]) == "read_write"


@pytest.mark.asyncio
async def test_only_an_org_admin_may_approve_a_unit_grant(tree):
    """A unit that could grant itself an integration has no grant. Approving is a
    second door into the same write, so it carries the same rule."""
    request = {
        "type": "connector_access",
        "tenantId": tree["org"],
        "workspaceId": tree["unit"],
        "projectId": None,
        "currentApproverRole": "bu_admin",
        "payload": {"targetId": "jira", "kind": "connector",
                    "access": "read_write", "scope": "unit"},
    }
    async with get_db_session_for_tenant(tree["org"]) as s:
        with pytest.raises(EffectNotAvailable):
            await apply_on_approve(s, request)


@pytest.mark.asyncio
async def test_approval_cannot_exceed_the_units_grant(tree):
    """The ceiling holds through approval too, or approving would be the way round
    the hierarchy the direct endpoint refuses."""
    c = _client()
    c.post("/integrations/access", headers=_admin(tree),
           params={"kind": "connector", "id": "jira",
                   "workspaceId": tree["unit"], "access": "read"})

    request = {
        "type": "connector_access",
        "tenantId": tree["org"],
        "workspaceId": tree["unit"],
        "projectId": tree["project"],
        "currentApproverRole": "bu_admin",
        "payload": {"targetId": "jira", "kind": "connector",
                    "access": "read_write", "scope": "project"},
    }
    async with get_db_session_for_tenant(tree["org"]) as s:
        with pytest.raises(EffectNotAvailable) as exc:
            await apply_on_approve(s, request)
    assert "read-only" in str(exc.value)
    assert await _effective(tree["org"], tree["project"]) == "read"


@pytest.mark.asyncio
async def test_approving_for_an_ungranted_unit_is_refused(tree):
    request = {
        "type": "connector_access",
        "tenantId": tree["org"],
        "workspaceId": tree["unit"],
        "projectId": tree["project"],
        "currentApproverRole": "bu_admin",
        "payload": {"targetId": "slack", "kind": "connector",
                    "access": "read", "scope": "project"},
    }
    async with get_db_session_for_tenant(tree["org"]) as s:
        with pytest.raises(EffectNotAvailable):
            await apply_on_approve(s, request)


@pytest.mark.asyncio
async def test_a_request_naming_no_level_is_refused(tree):
    """An approval that cannot take effect is refused, not recorded."""
    request = {
        "type": "connector_access",
        "tenantId": tree["org"],
        "workspaceId": tree["unit"],
        "projectId": None,
        "currentApproverRole": "org_admin",
        "payload": {"targetId": "jira", "scope": "unit"},
    }
    async with get_db_session_for_tenant(tree["org"]) as s:
        with pytest.raises(EffectNotAvailable):
            await apply_on_approve(s, request)
