"""GET/PUT /model/providers/grants — the model-provider-specific grant shape.

Task 2 widened the generic /integrations/access routes (integration_access.py) to
accept kind='model_provider'. This is a purpose-built pair living in model.py that
reads/writes the same integration_grants rows, giving the frontend a
{provider, businessUnitIds[]} shape instead of the generic connector one. Mirrors
list_connector_grants/set_connector_grants (integration_access.py), except PUT here
is always per-BU — no whole-policy replace mode, since the Org Admin's grant UI
always acts per business unit for providers.
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
    org_id = str(_uuid.uuid4())
    payments, lending = str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Model Grants Test')"
        ), {"i": org_id, "s": f"mgt-{org_id[:8]}"})
        for wid, slug, name in ((payments, "payments", "Payments"), (lending, "lending", "Lending")):
            await s.execute(text(
                "INSERT INTO workspaces (id, organization_id, slug, display_name) "
                "VALUES (:i, :o, :s, :n)"
            ), {"i": wid, "o": org_id, "s": slug, "n": name})
    yield {"org": org_id, "payments": payments, "lending": lending}


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


@pytest.mark.asyncio
async def test_get_and_put_model_provider_grants(org):
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])

    # Initially empty.
    resp = c.get("/model/providers/grants", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []

    # Grant anthropic to the payments workspace.
    resp2 = c.put(
        "/model/providers/grants",
        headers=headers,
        params={"workspaceId": org["payments"]},
        json={"providers": ["anthropic"]},
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json() == [{"provider": "anthropic", "businessUnitIds": [org["payments"]]}]

    # Read back org-wide.
    resp3 = c.get("/model/providers/grants", headers=headers)
    assert resp3.json() == [{"provider": "anthropic", "businessUnitIds": [org["payments"]]}]

    # PUT replaces the whole set for that BU, not additive: swap to a different
    # provider and the old one should disappear.
    resp4 = c.put(
        "/model/providers/grants",
        headers=headers,
        params={"workspaceId": org["payments"]},
        json={"providers": ["openai"]},
    )
    assert resp4.status_code == 200, resp4.text
    assert resp4.json() == [{"provider": "openai", "businessUnitIds": [org["payments"]]}]

    resp5 = c.get("/model/providers/grants", headers=headers)
    providers = {g["provider"] for g in resp5.json()}
    assert providers == {"openai"}


@pytest.mark.asyncio
async def test_put_aggregates_across_workspaces(org):
    c = TestClient(process_api.app)
    admin = await _user(org, "orgadmin")
    headers = _headers(admin, org["org"], ["admin:*"])

    c.put("/model/providers/grants", headers=headers,
          params={"workspaceId": org["payments"]}, json={"providers": ["anthropic"]})
    c.put("/model/providers/grants", headers=headers,
          params={"workspaceId": org["lending"]}, json={"providers": ["anthropic", "openai"]})

    body = c.get("/model/providers/grants", headers=headers).json()
    by_provider = {g["provider"]: sorted(g["businessUnitIds"]) for g in body}
    assert by_provider["anthropic"] == sorted([org["payments"], org["lending"]])
    assert by_provider["openai"] == [org["lending"]]


@pytest.mark.asyncio
async def test_bu_admin_cannot_set_model_provider_grants(org):
    """The org-admin-only gate: a BU Admin holding model:manage (for their own unit's
    provider config) still may not decide which providers a BU is granted."""
    c = TestClient(process_api.app)
    bua = await _user(org, "farah")
    await grant_role(bua, org["payments"], "bu_admin",
                     tenant_id=org["org"], scope_kind="business_unit")
    headers = _headers(bua, org["org"], ["model:manage"])

    resp = c.put(
        "/model/providers/grants",
        headers=headers,
        params={"workspaceId": org["payments"]},
        json={"providers": ["anthropic"]},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_no_model_manage_permission_is_rejected_at_the_router(org):
    """The router-level floor: without model:manage at all, even GET is refused."""
    c = TestClient(process_api.app)
    nobody = await _user(org, "nobody")
    headers = _headers(nobody, org["org"], [])

    resp = c.get("/model/providers/grants", headers=headers)
    assert resp.status_code == 403, resp.text
