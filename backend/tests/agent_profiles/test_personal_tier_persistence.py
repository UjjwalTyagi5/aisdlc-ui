"""Live-DB end-to-end: a personal (user-scope) Behavior default can be drafted,
published, and read back — and only by its own owner. Agent Studio sub-project 2."""
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
async def test_personal_behavior_default_round_trips(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "developer", "project", str(uuid.uuid4()))
    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        draft = await client.post(
            "/agent-profiles/draft",
            json={
                "agent_id": "requirements", "scope": "user", "scope_id": user_id,
                "prompt_prepend": "Always ask about compliance constraints first.",
            },
            headers=headers,
        )
        assert draft.status_code == 200
        draft_id = draft.json()["id"]

        published = await client.post(f"/agent-profiles/{draft_id}/publish", headers=headers)
        assert published.status_code == 200

        summary = await client.get(
            "/agent-profiles/summary", params={"scope": "user", "scope_id": user_id}, headers=headers,
        )
        assert summary.status_code == 200
        entry = next(a for a in summary.json()["agents"] if a["agent_id"] == "requirements")
        assert entry["inherited_from"] is None
        assert entry["active"]["prompt_prepend"] == "Always ask about compliance constraints first."


@pytest.mark.asyncio
async def test_personal_behavior_default_inherits_from_project(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "developer", "project", project_id)
    dev_token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])

    project_admin_id = str(uuid.uuid4())
    await _bind_role(tenant, project_admin_id, "project_admin", "project", project_id)
    # Needs both: "draft" is gated on skill:edit and "publish" on workspace:manage
    # (assert_can_write_agent_scope, agent_profiles.py) — this actor performs both.
    pa_token = mint_token(
        user_id=project_admin_id, tenant_id=tenant,
        permissions=["artifact:view", "skill:edit", "workspace:manage"],
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        draft = await client.post(
            "/agent-profiles/draft",
            json={
                "agent_id": "requirements", "scope": "project", "scope_id": project_id,
                "prompt_prepend": "Project-wide default.",
            },
            headers={"Authorization": f"Bearer {pa_token}"},
        )
        assert draft.status_code == 200
        publish = await client.post(
            f"/agent-profiles/{draft.json()['id']}/publish",
            headers={"Authorization": f"Bearer {pa_token}"},
        )
        assert publish.status_code == 200

        summary = await client.get(
            "/agent-profiles/summary",
            params={"scope": "user", "scope_id": user_id, "project_id": project_id},
            headers={"Authorization": f"Bearer {dev_token}"},
        )
        assert summary.status_code == 200
        entry = next(a for a in summary.json()["agents"] if a["agent_id"] == "requirements")
        assert entry["inherited_from"] == "project"
        assert entry["active"]["prompt_prepend"] == "Project-wide default."
