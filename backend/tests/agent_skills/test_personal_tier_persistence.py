"""Live-DB end-to-end: a personal (user-scope) custom skill can be created, toggled,
and read back — and only by its own owner. Agent Studio sub-project 2."""
import uuid

import httpx
import pytest
from sqlalchemy import text

from process_api import app
from shared.db import get_db_session_for_tenant


async def _bind_role(tenant_id: str, user_id: str, role: str, scope_kind: str, scope_id: str | None) -> None:
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
async def test_personal_skill_round_trips(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "contributor", "business_unit", str(uuid.uuid4()))
    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/agent-skills",
            json={
                "agent_id": "requirements", "scope": "user", "scope_id": user_id,
                "skill_key": "my-checklist", "display_name": "My Checklist",
                "body": "Always double check acceptance criteria.",
            },
            headers=headers,
        )
        assert created.status_code == 200

        listed = await client.get(
            "/agent-skills", params={"agent_id": "requirements", "scope": "user", "scope_id": user_id},
            headers=headers,
        )
        assert listed.status_code == 200
        hit = next(s for s in listed.json()["skills"] if s["skill_key"] == "my-checklist")
        assert hit["origin_scope"] == "user"
        assert hit["editable"] is True


@pytest.mark.asyncio
async def test_someone_elses_personal_skill_write_is_denied(mint_token):
    tenant = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    attacker_id = str(uuid.uuid4())
    await _bind_role(tenant, attacker_id, "developer", "project", str(uuid.uuid4()))
    token = mint_token(user_id=attacker_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills",
            json={
                "agent_id": "requirements", "scope": "user", "scope_id": owner_id,
                "skill_key": "sneaky", "display_name": "Sneaky", "body": "x",
            },
            headers=headers,
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_someone_elses_personal_skill_read_is_denied(mint_token):
    """Final whole-branch review finding C1: scope=user reads had no ownership
    check at all — any authenticated caller could read another user's personal
    skill catalog by supplying their scope_id. Covers list, detail, and versions."""
    tenant = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    attacker_id = str(uuid.uuid4())
    await _bind_role(tenant, owner_id, "developer", "project", str(uuid.uuid4()))
    await _bind_role(tenant, attacker_id, "developer", "project", str(uuid.uuid4()))
    owner_token = mint_token(user_id=owner_id, tenant_id=tenant, permissions=["artifact:view"])
    attacker_token = mint_token(user_id=attacker_id, tenant_id=tenant, permissions=["artifact:view"])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/agent-skills",
            json={
                "agent_id": "requirements", "scope": "user", "scope_id": owner_id,
                "skill_key": "private-notes", "display_name": "Private Notes", "body": "shh",
            },
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert created.status_code == 200

        listed = await client.get(
            "/agent-skills", params={"agent_id": "requirements", "scope": "user", "scope_id": owner_id},
            headers={"Authorization": f"Bearer {attacker_token}"},
        )
        assert listed.status_code == 403

        detail = await client.get(
            "/agent-skills/custom/private-notes",
            params={"agent_id": "requirements", "scope": "user", "scope_id": owner_id},
            headers={"Authorization": f"Bearer {attacker_token}"},
        )
        assert detail.status_code == 403

        versions = await client.get(
            "/agent-skills/private-notes/versions",
            params={"agent_id": "requirements", "scope": "user", "scope_id": owner_id},
            headers={"Authorization": f"Bearer {attacker_token}"},
        )
        assert versions.status_code == 403

        # Sanity: the owner reading their own scope still works.
        own_listed = await client.get(
            "/agent-skills", params={"agent_id": "requirements", "scope": "user", "scope_id": owner_id},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert own_listed.status_code == 200


@pytest.mark.asyncio
async def test_toggle_finds_project_inherited_skill_at_personal_tier(mint_token):
    """Final whole-branch review finding I3: toggle_skill's ancestor walk still
    called ancestor_chain with 3 args (no project_id), so a personal tier could
    never find a skill it inherits from its project — list_skills would show it,
    but toggling it 404'd. ToggleIn now carries project_id, threaded through."""
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "developer", "project", project_id)
    dev_token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])

    project_admin_id = str(uuid.uuid4())
    await _bind_role(tenant, project_admin_id, "project_admin", "project", project_id)
    pa_token = mint_token(
        user_id=project_admin_id, tenant_id=tenant, permissions=["artifact:view", "skill:edit"],
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/agent-skills",
            json={
                "agent_id": "requirements", "scope": "project", "scope_id": project_id,
                "skill_key": "project-checklist", "display_name": "Project Checklist", "body": "check it",
            },
            headers={"Authorization": f"Bearer {pa_token}"},
        )
        assert created.status_code == 200

        toggled = await client.post(
            "/agent-skills/toggle",
            json={
                "agent_id": "requirements", "scope": "user", "scope_id": user_id,
                "origin": "custom", "skill_key": "project-checklist", "enabled": False,
                "project_id": project_id,
            },
            headers={"Authorization": f"Bearer {dev_token}"},
        )
        assert toggled.status_code == 200
        assert toggled.json()["enabled"] is False
