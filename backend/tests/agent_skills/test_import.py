import uuid

import httpx
import pytest

from process_api import app
from shared.db import get_db_session_for_tenant, get_db_session_superuser
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
async def test_import_from_administered_bu_succeeds(mint_token):
    tenant = str(uuid.uuid4())
    admin_id = str(uuid.uuid4())
    source_ws = str(uuid.uuid4())
    target_ws = str(uuid.uuid4())
    await _bind_role(tenant, admin_id, "bu_admin", "business_unit", source_ws)
    await _bind_role(tenant, admin_id, "bu_admin", "business_unit", target_ws)
    token = mint_token(user_id=admin_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/import",
            json={
                "agent_id": "requirements", "scope": "workspace", "scope_id": target_ws,
                "skill_key": "imported-skill", "display_name": "Imported Skill",
                "description": "d", "when_to_use": "w", "body": "x",
                "source": {"kind": "same_tenant_bu", "workspace_id": source_ws},
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["skill_key"] == "imported-skill"


@pytest.mark.asyncio
async def test_import_from_non_administered_bu_refused(mint_token):
    tenant = str(uuid.uuid4())
    admin_id = str(uuid.uuid4())
    target_ws = str(uuid.uuid4())
    not_administered_ws = str(uuid.uuid4())
    await _bind_role(tenant, admin_id, "bu_admin", "business_unit", target_ws)
    token = mint_token(user_id=admin_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/import",
            json={
                "agent_id": "requirements", "scope": "workspace", "scope_id": target_ws,
                "skill_key": "imported-skill-2", "display_name": "Imported",
                "description": "d", "when_to_use": "w", "body": "x",
                "source": {"kind": "same_tenant_bu", "workspace_id": not_administered_ws},
            },
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "SOURCE_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_import_with_credential_in_body_refused(mint_token):
    tenant = str(uuid.uuid4())
    admin_id = str(uuid.uuid4())
    ws = str(uuid.uuid4())
    await _bind_role(tenant, admin_id, "bu_admin", "business_unit", ws)
    token = mint_token(user_id=admin_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/import",
            json={
                "agent_id": "requirements", "scope": "workspace", "scope_id": ws,
                "skill_key": "leaky-skill", "display_name": "Leaky",
                "description": "d", "when_to_use": "w",
                "body": "token: ghp_abcdefghijklmnopqrstuvwxyz0123456789",
                "source": {"kind": "same_tenant_bu", "workspace_id": ws},
            },
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "CREDENTIAL_DETECTED"


@pytest.mark.asyncio
async def test_import_from_unlisted_external_source_refused(mint_token):
    tenant = str(uuid.uuid4())
    admin_id = str(uuid.uuid4())
    ws = str(uuid.uuid4())
    await _bind_role(tenant, admin_id, "bu_admin", "business_unit", ws)
    token = mint_token(user_id=admin_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/import",
            json={
                "agent_id": "requirements", "scope": "workspace", "scope_id": ws,
                "skill_key": "ext-skill", "display_name": "External",
                "description": "d", "when_to_use": "w", "body": "x",
                "source": {"kind": "external", "url": "https://untrusted.example.com/skill.md"},
            },
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "SOURCE_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_import_from_allowlisted_external_source_succeeds(mint_token):
    tenant = str(uuid.uuid4())
    admin_id = str(uuid.uuid4())
    ws = str(uuid.uuid4())
    await _bind_role(tenant, admin_id, "bu_admin", "business_unit", ws)
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(text(
            "INSERT INTO import_source_allowlist (id, tenant_id, source_pattern, label, created_by) "
            "VALUES (gen_random_uuid(), CAST(:t AS uuid), :p, 'Trusted', 'org-admin-1')"
        ), {"t": tenant, "p": "https://trusted.example.com/"})
    token = mint_token(user_id=admin_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/import",
            json={
                "agent_id": "requirements", "scope": "workspace", "scope_id": ws,
                "skill_key": "ext-skill-2", "display_name": "External Trusted",
                "description": "d", "when_to_use": "w", "body": "x",
                "source": {"kind": "external", "url": "https://trusted.example.com/skills/foo.md"},
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_import_external_subdomain_confusion_refused(mint_token):
    """A no-trailing-slash allowlist pattern must not match a URL where the
    pattern's characters are merely a substring prefix of a longer, different
    host (e.g. trusted.example.com.evil.com) — plain startswith() would wrongly
    allow this."""
    tenant = str(uuid.uuid4())
    admin_id = str(uuid.uuid4())
    ws = str(uuid.uuid4())
    await _bind_role(tenant, admin_id, "bu_admin", "business_unit", ws)
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(text(
            "INSERT INTO import_source_allowlist (id, tenant_id, source_pattern, label, created_by) "
            "VALUES (gen_random_uuid(), CAST(:t AS uuid), :p, 'Trusted', 'org-admin-1')"
        ), {"t": tenant, "p": "https://trusted.example.com"})
    token = mint_token(user_id=admin_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/import",
            json={
                "agent_id": "requirements", "scope": "workspace", "scope_id": ws,
                "skill_key": "ext-skill-3", "display_name": "External Confusable",
                "description": "d", "when_to_use": "w", "body": "x",
                "source": {"kind": "external", "url": "https://trusted.example.com.evil.com/payload"},
            },
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "SOURCE_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_import_external_no_trailing_slash_pattern_still_allows_subpaths(mint_token):
    """The boundary fix must not over-correct: a bare-domain pattern with no
    trailing slash should still allow a real subpath under that domain."""
    tenant = str(uuid.uuid4())
    admin_id = str(uuid.uuid4())
    ws = str(uuid.uuid4())
    await _bind_role(tenant, admin_id, "bu_admin", "business_unit", ws)
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(text(
            "INSERT INTO import_source_allowlist (id, tenant_id, source_pattern, label, created_by) "
            "VALUES (gen_random_uuid(), CAST(:t AS uuid), :p, 'Trusted', 'org-admin-1')"
        ), {"t": tenant, "p": "https://trusted.example.com"})
    token = mint_token(user_id=admin_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/import",
            json={
                "agent_id": "requirements", "scope": "workspace", "scope_id": ws,
                "skill_key": "ext-skill-4", "display_name": "External Subpath",
                "description": "d", "when_to_use": "w", "body": "x",
                "source": {"kind": "external", "url": "https://trusted.example.com/anything"},
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_import_external_empty_allowlist_pattern_does_not_wildcard(mint_token):
    """A stray empty-string source_pattern row must not act as a wildcard that
    lets every external URL through (Python's ''.startswith("") is always
    True, so this must be filtered before the match check)."""
    tenant = str(uuid.uuid4())
    admin_id = str(uuid.uuid4())
    ws = str(uuid.uuid4())
    await _bind_role(tenant, admin_id, "bu_admin", "business_unit", ws)
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(text(
            "INSERT INTO import_source_allowlist (id, tenant_id, source_pattern, label, created_by) "
            "VALUES (gen_random_uuid(), CAST(:t AS uuid), :p, 'Empty', 'org-admin-1')"
        ), {"t": tenant, "p": ""})
    token = mint_token(user_id=admin_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/import",
            json={
                "agent_id": "requirements", "scope": "workspace", "scope_id": ws,
                "skill_key": "ext-skill-5", "display_name": "External Unlisted",
                "description": "d", "when_to_use": "w", "body": "x",
                "source": {"kind": "external", "url": "https://untrusted.example.com/skill.md"},
            },
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "SOURCE_NOT_ALLOWED"
