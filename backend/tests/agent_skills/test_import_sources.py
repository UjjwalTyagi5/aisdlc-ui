import uuid

import httpx
import pytest

from process_api import app
from shared.db import get_db_session_for_tenant
from sqlalchemy import text


async def _bind_role(tenant_id, user_id, role, scope_kind, scope_id):
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(text(
            "INSERT INTO users (id, email, password_hash, tenant_id, active) "
            "VALUES (:i, :e, 'x', CAST(:t AS uuid), true) ON CONFLICT (id) DO NOTHING"
        ), {"i": user_id, "e": f"{user_id}@example.com", "t": tenant_id})
        await s.execute(text(
            "INSERT INTO role_bindings (id, user_id, scope_kind, scope_id, role_name, tenant_id) "
            "VALUES (gen_random_uuid(), :u, :sk, CAST(:si AS uuid), :r, CAST(:t AS uuid))"
        ), {"u": user_id, "sk": scope_kind, "si": scope_id, "r": role, "t": tenant_id})


@pytest.mark.asyncio
async def test_org_admin_can_add_an_allowlist_entry(mint_token):
    tenant = str(uuid.uuid4())
    org_admin_id = str(uuid.uuid4())
    token = mint_token(user_id=org_admin_id, tenant_id=tenant, permissions=["artifact:view", "admin:*"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/agent-skills/import-sources",
            json={"source_pattern": "https://github.com/acme-org/", "label": "Acme skill library"},
            headers=headers,
        )
        assert created.status_code == 201, created.text

        listed = await client.get("/agent-skills/import-sources", headers=headers)
        assert listed.status_code == 200
        assert any(e["source_pattern"] == "https://github.com/acme-org/" for e in listed.json()["sources"])


@pytest.mark.asyncio
async def test_org_admin_cannot_add_a_degenerate_pattern_that_would_wildcard_the_screen(mint_token):
    """Regression: `_matches_import_source`'s boundary rule means a pattern
    with no real host (e.g. "https:", no "//") matches ANY url of that scheme
    ("https://evil.com/x" starts with "https:", and the next character is
    "/") -- silently wildcarding the whole external-source screen tenant-wide.
    Write-time validation must refuse this before it ever reaches the table
    (final whole-branch review, sub-project 5, Important #2)."""
    tenant = str(uuid.uuid4())
    org_admin_id = str(uuid.uuid4())
    token = mint_token(user_id=org_admin_id, tenant_id=tenant, permissions=["artifact:view", "admin:*"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for degenerate in ("https:", "http:", "ftp://x.com/", "https://x"):
            resp = await client.post(
                "/agent-skills/import-sources",
                json={"source_pattern": degenerate, "label": "Too broad"},
                headers=headers,
            )
            assert resp.status_code == 422, f"{degenerate!r} should have been refused: {resp.text}"
            assert resp.json()["detail"]["code"] == "INVALID_SOURCE_PATTERN"


@pytest.mark.asyncio
async def test_non_org_admin_cannot_add_an_allowlist_entry(mint_token):
    tenant = str(uuid.uuid4())
    bu_admin_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    await _bind_role(tenant, bu_admin_id, "bu_admin", "business_unit", ws_id)
    token = mint_token(user_id=bu_admin_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/import-sources",
            json={"source_pattern": "https://evil.example.com/", "label": "Self-approved"},
            headers=headers,
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_any_member_can_list_allowlist_entries(mint_token):
    tenant = str(uuid.uuid4())
    dev_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    await _bind_role(tenant, dev_id, "developer", "project", project_id)
    token = mint_token(user_id=dev_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/agent-skills/import-sources", headers=headers)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_org_admin_cannot_add_a_whitespace_only_pattern(mint_token):
    tenant = str(uuid.uuid4())
    org_admin_id = str(uuid.uuid4())
    token = mint_token(user_id=org_admin_id, tenant_id=tenant, permissions=["artifact:view", "admin:*"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/import-sources",
            json={"source_pattern": "   ", "label": "Whitespace only"},
            headers=headers,
        )
        assert resp.status_code == 422
