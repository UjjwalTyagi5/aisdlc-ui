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


async def _create_draft(client, headers, scope, scope_id, body_extra=""):
    resp = await client.post(
        "/agent-profiles/draft",
        json={
            "agent_id": "requirements", "scope": scope, "scope_id": scope_id,
            "prompt_prepend": "Cover acceptance criteria, stakeholder input, scope, and user stories. " + body_extra,
            "prompt_append": "", "output_contract_extra": "",
        },
        headers=headers,
    )
    # /agent-profiles/draft has no explicit status_code (FastAPI's POST default,
    # 200) — unrelated to this task, unchanged, and consistent with every other
    # test in this package that exercises it (e.g. test_agent_profiles_router.py).
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("purge_created_orgs")
async def test_evaluate_then_propose_succeeds(mint_token):
    # A real organizations/workspaces/projects row is required: propose()'s
    # project-scope branch resolves the governance request's workspace_id by
    # querying `projects` for this exact project id (see
    # test_agent_profiles_router.py::test_propose_allowed_for_project_member_with_no_permission_string,
    # which needs the same bootstrap for the same reason) — a bare, never-persisted
    # project uuid resolves to no workspace and 422s with NO_WORKSPACE.
    tenant = str(uuid.uuid4())
    dev_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) "
            "VALUES (CAST(:i AS uuid), :s, 'Evaluation Gate Test') ON CONFLICT (id) DO NOTHING"
        ), {"i": tenant, "s": f"eval-gate-{tenant}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (CAST(:i AS uuid), CAST(:o AS uuid), :s, 'Test Workspace') "
            "ON CONFLICT (id) DO NOTHING"
        ), {"i": ws_id, "o": tenant, "s": f"ws-{ws_id}"})
        await s.execute(text(
            "INSERT INTO projects (id, tenant_id, workspace_id, display_name) "
            "VALUES (CAST(:i AS uuid), CAST(:t AS uuid), CAST(:w AS uuid), 'Test Project') "
            "ON CONFLICT (id) DO NOTHING"
        ), {"i": project_id, "t": tenant, "w": ws_id})

    await _bind_role(tenant, dev_id, "developer", "project", project_id)
    token = mint_token(user_id=dev_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        draft_id = await _create_draft(client, headers, "project", project_id)
        evaluated = await client.post(f"/agent-profiles/{draft_id}/evaluate", headers=headers)
        assert evaluated.status_code == 201, evaluated.text
        assert evaluated.json()["result"] == "pass"

        proposed = await client.post(f"/agent-profiles/{draft_id}/propose", headers=headers)
        assert proposed.status_code == 201, proposed.text


@pytest.mark.asyncio
async def test_propose_refused_without_a_passing_evaluation(mint_token):
    tenant = str(uuid.uuid4())
    dev_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    await _bind_role(tenant, dev_id, "developer", "project", project_id)
    token = mint_token(user_id=dev_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        draft_id = await _create_draft(client, headers, "project", project_id)
        proposed = await client.post(f"/agent-profiles/{draft_id}/propose", headers=headers)
        assert proposed.status_code == 422
        assert proposed.json()["detail"]["code"] == "EVALUATION_REQUIRED"


@pytest.mark.asyncio
async def test_org_scope_self_evaluation_blocked(mint_token):
    tenant = str(uuid.uuid4())
    bu_admin_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    await _bind_role(tenant, bu_admin_id, "bu_admin", "business_unit", ws_id)
    token = mint_token(user_id=bu_admin_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        draft_id = await _create_draft(client, headers, "org", None)
        evaluated = await client.post(f"/agent-profiles/{draft_id}/evaluate", headers=headers)
        assert evaluated.status_code == 403
        assert evaluated.json()["detail"]["code"] == "SELF_EVALUATION_BLOCKED"


@pytest.mark.asyncio
async def test_org_scope_evaluation_by_a_different_actor_succeeds(mint_token):
    tenant = str(uuid.uuid4())
    author_id = str(uuid.uuid4())
    evaluator_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    await _bind_role(tenant, author_id, "bu_admin", "business_unit", ws_id)
    await _bind_role(tenant, evaluator_id, "bu_admin", "business_unit", str(uuid.uuid4()))
    author_token = mint_token(user_id=author_id, tenant_id=tenant, permissions=["artifact:view"])
    evaluator_token = mint_token(user_id=evaluator_id, tenant_id=tenant, permissions=["artifact:view"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        draft_id = await _create_draft(client, {"Authorization": f"Bearer {author_token}"}, "org", None)
        evaluated = await client.post(
            f"/agent-profiles/{draft_id}/evaluate",
            headers={"Authorization": f"Bearer {evaluator_token}"},
        )
        assert evaluated.status_code == 201, evaluated.text
