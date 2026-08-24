"""POST /model/providers/{provider_id}/assign — Task 5.

A BU Admin pushes a `model_providers` row they already created (Task 4's BU-scoped,
key-required flow) onto one of their own projects, populating that project's
ProjectModelSelection.selected (the existing project-model-selection mechanism,
unmodified schema) so the project's own admin can later pick it as their
default/master key (Task 12, not this task).

Mirrors the httpx.AsyncClient + mint_token + _seed_org_workspace_project pattern used
by test_model_config_api.py's Task 4 tests, and reuses that same seeding helper.
"""
import uuid

import pytest
from sqlalchemy import text

from tests.test_model_grants import _seed_org_workspace_project

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


def _headers(mint_token, user_id: str, tenant_id: str, permissions: list[str]) -> dict:
    return {"Authorization": f"Bearer {mint_token(user_id=user_id, tenant_id=tenant_id, permissions=permissions)}"}


@pytest.mark.asyncio
async def test_bu_admin_assigns_key_to_project(mint_token):
    """Happy path: a BU Admin assigns their own already-created key to their own
    project. `selected` gets one entry per enabled offering; an already-set
    `defaultKey` is left untouched."""
    import httpx
    from process_api import app
    from shared.authz.grant import grant_role
    from shared.db import get_db_session_for_tenant

    tenant = str(uuid.uuid4())
    ws_id, proj_id = await _seed_org_workspace_project(tenant, "Unit A")

    bu_admin = f"bu-admin-{uuid.uuid4()}"
    await grant_role(bu_admin, ws_id, "bu_admin", tenant_id=tenant, scope_kind="business_unit")
    bu_headers = _headers(mint_token, bu_admin, tenant, ["model:manage"])

    org_admin = f"org-admin-{uuid.uuid4()}"
    org_headers = _headers(mint_token, org_admin, tenant, ["admin:*"])

    # Pre-seed an existing defaultKey directly (bypassing set_project_selection's
    # allowed-set validation, which isn't the point of this test) so we can assert
    # assign leaves it alone.
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(
            text(
                "INSERT INTO project_model_selections (id, tenant_id, project_id, selected, default_key, updated_at) "
                "VALUES (:id, :t, :p, '[]'::jsonb, :dk, now())"
            ),
            {"id": str(uuid.uuid4()), "t": tenant, "p": proj_id, "dk": "pre-existing-default"},
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        grant_resp = await client.put(
            "/model/providers/grants", params={"workspaceId": ws_id}, json={"providers": ["anthropic"]},
            headers=org_headers,
        )
        assert grant_resp.status_code == 200, grant_resp.text

        created = await client.post(
            "/model/providers",
            json={
                "provider": "anthropic", "display_name": "Payments prod", "api_key": "sk-test-123",
                "models": [{"model_id": "claude-sonnet-4-6"}], "workspaceId": ws_id,
            },
            headers=bu_headers,
        )
        assert created.status_code == 201, created.text
        provider_id = created.json()["id"]

        resp = await client.post(
            f"/model/providers/{provider_id}/assign",
            json={"projectId": proj_id},
            headers=bu_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

    selected = body["selected"]
    assert any(
        e["provider"] == "anthropic" and e["model_id"] == "claude-sonnet-4-6" and e["credentialId"] == provider_id
        for e in selected
    ), selected
    # defaultKey must be untouched.
    assert body["defaultKey"] == "pre-existing-default"


@pytest.mark.asyncio
async def test_assigning_the_same_credential_twice_does_not_duplicate(mint_token):
    """Calling assign twice for the same provider must not double up `selected`."""
    import httpx
    from process_api import app
    from shared.authz.grant import grant_role

    tenant = str(uuid.uuid4())
    ws_id, proj_id = await _seed_org_workspace_project(tenant, "Unit A")

    bu_admin = f"bu-admin-{uuid.uuid4()}"
    await grant_role(bu_admin, ws_id, "bu_admin", tenant_id=tenant, scope_kind="business_unit")
    bu_headers = _headers(mint_token, bu_admin, tenant, ["model:manage"])

    org_admin = f"org-admin-{uuid.uuid4()}"
    org_headers = _headers(mint_token, org_admin, tenant, ["admin:*"])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.put(
            "/model/providers/grants", params={"workspaceId": ws_id}, json={"providers": ["anthropic"]},
            headers=org_headers,
        )
        created = await client.post(
            "/model/providers",
            json={
                "provider": "anthropic", "display_name": "Payments prod", "api_key": "sk-test-123",
                "models": [{"model_id": "claude-sonnet-4-6"}], "workspaceId": ws_id,
            },
            headers=bu_headers,
        )
        provider_id = created.json()["id"]

        first = await client.post(
            f"/model/providers/{provider_id}/assign", json={"projectId": proj_id}, headers=bu_headers,
        )
        assert first.status_code == 200, first.text
        second = await client.post(
            f"/model/providers/{provider_id}/assign", json={"projectId": proj_id}, headers=bu_headers,
        )
        assert second.status_code == 200, second.text

    matches = [
        e for e in second.json()["selected"]
        if e["provider"] == "anthropic" and e["model_id"] == "claude-sonnet-4-6" and e["credentialId"] == provider_id
    ]
    assert len(matches) == 1, second.json()["selected"]


@pytest.mark.asyncio
async def test_assign_rejects_a_project_the_caller_does_not_administer(mint_token):
    """A BU Admin of Unit A cannot push a key onto a project outside their unit —
    the project-ownership check at the router (_require_scoped) must bite first."""
    import httpx
    from process_api import app
    from shared.authz.grant import grant_role

    tenant = str(uuid.uuid4())
    ws_a, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    ws_b, proj_b = await _seed_org_workspace_project(tenant, "Unit B")

    bu_admin_a = f"bu-admin-a-{uuid.uuid4()}"
    await grant_role(bu_admin_a, ws_a, "bu_admin", tenant_id=tenant, scope_kind="business_unit")
    bu_a_headers = _headers(mint_token, bu_admin_a, tenant, ["model:manage"])

    org_admin = f"org-admin-{uuid.uuid4()}"
    org_headers = _headers(mint_token, org_admin, tenant, ["admin:*"])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.put(
            "/model/providers/grants", params={"workspaceId": ws_a}, json={"providers": ["anthropic"]},
            headers=org_headers,
        )
        created = await client.post(
            "/model/providers",
            json={
                "provider": "anthropic", "display_name": "Payments prod", "api_key": "sk-test-123",
                "models": [{"model_id": "claude-sonnet-4-6"}], "workspaceId": ws_a,
            },
            headers=bu_a_headers,
        )
        provider_id = created.json()["id"]

        # bu_admin_a does not administer Unit B / proj_b at all.
        resp = await client.post(
            f"/model/providers/{provider_id}/assign",
            json={"projectId": proj_b},
            headers=bu_a_headers,
        )
    assert resp.status_code in (403, 404), resp.text

    # An arbitrary (non-UUID) provider id against a foreign project must also be
    # refused, not 500 — the ownership gate should bite before the provider lookup.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp2 = await client.post(
            "/model/providers/some-id/assign", json={"projectId": proj_b}, headers=bu_a_headers,
        )
    assert resp2.status_code in (403, 404), resp2.text


@pytest.mark.asyncio
async def test_assign_rejects_a_provider_from_a_different_business_unit(mint_token):
    """Even a BU Admin who legitimately administers the TARGET project must not be
    able to push a provider connection that belongs to a DIFFERENT business unit onto
    it — the project-ownership check alone is not enough; the provider's own
    workspace_id must match the project's."""
    import httpx
    from process_api import app
    from shared.authz.grant import grant_role

    tenant = str(uuid.uuid4())
    ws_a, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    ws_b, proj_b = await _seed_org_workspace_project(tenant, "Unit B")

    bu_admin_a = f"bu-admin-a-{uuid.uuid4()}"
    await grant_role(bu_admin_a, ws_a, "bu_admin", tenant_id=tenant, scope_kind="business_unit")
    bu_a_headers = _headers(mint_token, bu_admin_a, tenant, ["model:manage"])

    bu_admin_b = f"bu-admin-b-{uuid.uuid4()}"
    await grant_role(bu_admin_b, ws_b, "bu_admin", tenant_id=tenant, scope_kind="business_unit")
    bu_b_headers = _headers(mint_token, bu_admin_b, tenant, ["model:manage"])

    org_admin = f"org-admin-{uuid.uuid4()}"
    org_headers = _headers(mint_token, org_admin, tenant, ["admin:*"])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.put(
            "/model/providers/grants", params={"workspaceId": ws_a}, json={"providers": ["anthropic"]},
            headers=org_headers,
        )
        created = await client.post(
            "/model/providers",
            json={
                "provider": "anthropic", "display_name": "Unit A's key", "api_key": "sk-test-123",
                "models": [{"model_id": "claude-sonnet-4-6"}], "workspaceId": ws_a,
            },
            headers=bu_a_headers,
        )
        provider_id = created.json()["id"]

        # bu_admin_b legitimately administers proj_b, but the provider is Unit A's.
        resp = await client.post(
            f"/model/providers/{provider_id}/assign",
            json={"projectId": proj_b},
            headers=bu_b_headers,
        )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_assign_requires_project_id(mint_token):
    import httpx
    from process_api import app
    from shared.authz.grant import grant_role

    tenant = str(uuid.uuid4())
    ws_id, _ = await _seed_org_workspace_project(tenant, "Unit A")
    bu_admin = f"bu-admin-{uuid.uuid4()}"
    await grant_role(bu_admin, ws_id, "bu_admin", tenant_id=tenant, scope_kind="business_unit")
    bu_headers = _headers(mint_token, bu_admin, tenant, ["model:manage"])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/model/providers/some-provider-id/assign", json={}, headers=bu_headers,
        )
    assert resp.status_code == 422, resp.text
