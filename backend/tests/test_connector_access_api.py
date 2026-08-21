"""The two doors into a connector grant, and the ceiling both respect.

There are exactly two ways access gets written: the direct endpoints, and approving
a `connector_access` governance request. A ceiling enforced at one and not the other
is not a ceiling — approving would simply be the way round it. These tests assert the
same refusal through both.
"""
import json as _json
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
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name, connectors) "
            "VALUES (:i, :w, :t, 'Ledger', CAST(:c AS jsonb))"
        ), {"i": proj, "w": unit, "t": org,
            "c": _json.dumps({"development": ["jira"]})})
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


async def _effective(org, project, ref="jira", agent_id="development"):
    async with get_db_session_for_tenant(org) as s:
        return await effective_access(
            s, tenant_id=org, project_id=project, target_ref=ref, agent_id=agent_id
        )


# ── the direct door ──────────────────────────────────────────────────────────

def test_a_grant_carries_no_level(tree):
    """Migration 0024. The three tests that stood here asserted the grant's default
    level, that a bad one was refused, and that re-granting changed it. A grant is
    reach only now, so the response states no level at all."""
    c = _client()
    r = c.post("/integrations/access", headers=_admin(tree),
               params={"kind": "connector", "id": "jira", "workspaceId": tree["unit"]})
    assert r.status_code == 200, r.text
    assert "access" not in r.json()


def test_regranting_is_idempotent(tree):
    """It used to be how an Org Admin changed the level. With no level to change it
    is simply the same state again, and must not error."""
    c = _client()
    base = {"kind": "connector", "id": "jira", "workspaceId": tree["unit"]}
    assert c.post("/integrations/access", headers=_admin(tree), params=base).status_code == 200
    assert c.post("/integrations/access", headers=_admin(tree), params=base).status_code == 200


@pytest.mark.asyncio
async def test_a_project_may_be_set_wider_than_any_grant(tree):
    """THE ESCALATION REFUSAL IS GONE, and this pins its absence deliberately.

    A 403 `exceeds_grant` used to come back here. The grant carries no level to
    exceed since migration 0024, so whoever administers the project decides — which
    is the trade the change made, not an oversight. If this starts failing, somebody
    reintroduced a ceiling; the migration's docstring is the thing to read first.
    """
    c = _client()
    c.post("/integrations/access", headers=_admin(tree),
           params={"kind": "connector", "id": "jira", "workspaceId": tree["unit"]})

    r = c.put("/projects/" + tree["project"] + "/integrations/access", headers=_admin(tree),
              json={"kind": "connector", "targetId": "jira", "access": "read_write"})
    assert r.status_code == 200, r.text
    assert await _effective(tree["org"], tree["project"]) == "read_write"


@pytest.mark.asyncio
async def test_a_project_may_be_narrowed_and_restored(tree):
    c = _client()
    c.post("/integrations/access", headers=_admin(tree),
           params={"kind": "connector", "id": "jira", "workspaceId": tree["unit"]})

    r = c.put(f"/projects/{tree['project']}/integrations/access", headers=_admin(tree),
              json={"kind": "connector", "targetId": "jira", "access": "read"})
    assert r.status_code == 200, r.text
    assert await _effective(tree["org"], tree["project"]) == "read"

    # Clearing the project default drops its stages back to the picker's own
    # default. Not a revoke — the unit still holds the grant.
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
async def test_the_listing_states_the_project_default_and_no_unit_level(tree):
    c = _client()
    c.post("/integrations/access", headers=_admin(tree),
           params={"kind": "connector", "id": "jira", "workspaceId": tree["unit"]})
    c.put(f"/projects/{tree['project']}/integrations/access", headers=_admin(tree),
          json={"kind": "connector", "targetId": "jira", "access": "read"})

    rows = c.get(f"/projects/{tree['project']}/integrations/access",
                 headers=_admin(tree)).json()
    row = next(r for r in rows if r["targetId"] == "jira")
    # No `unitAccess`: reporting one would be the API inventing a ceiling.
    assert "unitAccess" not in row
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
    # The note states no level: the request's `access` is deliberately NOT applied to
    # the grant, or approving would put a ceiling back by the side door.
    assert "granted to the business unit" in note
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
async def test_approval_is_no_longer_bounded_by_the_units_grant(tree):
    """The mirror of test_a_project_may_be_set_wider_than_any_grant, at the other door.

    This used to refuse: the ceiling held through approval so that approving could
    not be the way round a hierarchy the direct endpoint enforced. Both doors lost
    the ceiling together in migration 0024, which is the point — they must not
    disagree about what is allowed, whichever one is closed.
    """
    c = _client()
    c.post("/integrations/access", headers=_admin(tree),
           params={"kind": "connector", "id": "jira", "workspaceId": tree["unit"]})

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
        await apply_on_approve(s, request)
    assert await _effective(tree["org"], tree["project"]) == "read_write"


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
