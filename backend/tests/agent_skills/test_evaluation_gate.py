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
@pytest.mark.usefixtures("purge_created_orgs")
async def test_evaluate_then_propose_skill_succeeds(mint_token):
    # A real organizations/workspaces/projects row is required: propose_skill's
    # project-scope branch resolves the governance request's workspace_id via
    # `_project_workspace_id`, which queries the `projects` table for the given
    # project id — a bare, never-persisted project uuid resolves to no workspace
    # and 422s with NO_WORKSPACE (see test_agent_skills_router.py::
    # test_propose_skill_then_approve_activates_it, which needs the same bootstrap
    # for the same reason).
    tenant = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    member_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) "
            "VALUES (CAST(:i AS uuid), :s, 'Evaluation Gate Skill Test') ON CONFLICT (id) DO NOTHING"
        ), {"i": tenant, "s": f"eval-gate-skill-{tenant}"})
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

    await _bind_role(tenant, owner_id, "project_admin", "project", project_id)
    await _bind_role(tenant, member_id, "developer", "project", project_id)
    owner_token = mint_token(user_id=owner_id, tenant_id=tenant, permissions=["artifact:view"])
    member_token = mint_token(user_id=member_id, tenant_id=tenant, permissions=["artifact:view"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/agent-skills",
            json={
                "agent_id": "requirements", "scope": "project", "scope_id": project_id,
                "skill_key": "eval-me", "display_name": "Eval Me",
                "body": "Cover acceptance criteria, stakeholder input, scope, and user stories.",
            },
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert created.status_code == 200, created.text

        evaluated = await client.post(
            "/agent-skills/eval-me/evaluate",
            json={"agent_id": "requirements", "scope": "project", "scope_id": project_id},
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert evaluated.status_code == 201, evaluated.text
        assert evaluated.json()["result"] == "pass"

        proposed = await client.post(
            "/agent-skills/eval-me/propose",
            json={"agent_id": "requirements", "scope": "project", "scope_id": project_id},
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert proposed.status_code == 201, proposed.text


@pytest.mark.asyncio
async def test_propose_skill_refused_without_a_passing_evaluation(mint_token):
    tenant = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    member_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    await _bind_role(tenant, owner_id, "project_admin", "project", project_id)
    await _bind_role(tenant, member_id, "developer", "project", project_id)
    owner_token = mint_token(user_id=owner_id, tenant_id=tenant, permissions=["artifact:view"])
    member_token = mint_token(user_id=member_id, tenant_id=tenant, permissions=["artifact:view"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/agent-skills",
            json={
                "agent_id": "requirements", "scope": "project", "scope_id": project_id,
                "skill_key": "no-eval", "display_name": "No Eval", "body": "x",
            },
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert created.status_code == 200

        proposed = await client.post(
            "/agent-skills/no-eval/propose",
            json={"agent_id": "requirements", "scope": "project", "scope_id": project_id},
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert proposed.status_code == 422
        assert proposed.json()["detail"]["code"] == "EVALUATION_REQUIRED"


@pytest.mark.asyncio
async def test_evaluate_skill_by_a_different_actor_than_the_author_succeeds(mint_token):
    """Confirms the get_latest_draft_version created_by-optional fix: the evaluator
    is NOT the skill's author, so a created_by-filtered lookup (sub-project 3's
    propose_skill behavior, unchanged there) would find nothing — evaluate must use
    the unfiltered variant."""
    tenant = str(uuid.uuid4())
    author_id = str(uuid.uuid4())
    evaluator_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    await _bind_role(tenant, author_id, "bu_admin", "business_unit", ws_id)
    await _bind_role(tenant, evaluator_id, "bu_admin", "business_unit", str(uuid.uuid4()))
    author_token = mint_token(user_id=author_id, tenant_id=tenant, permissions=["artifact:view"])
    evaluator_token = mint_token(user_id=evaluator_id, tenant_id=tenant, permissions=["artifact:view"])
    # Random per-run key (final whole-branch review, sub-project 4, Minor #4):
    # this test's tenant is a fresh UUID with no organizations row to scope a
    # cleanup fixture to, so a literal skill_key here would collide with
    # leftover residue from a prior local run against a persistent dev DB
    # whose app-role session bypasses RLS. A random key makes the test
    # order-independent regardless of environment.
    skill_key = f"org-skill-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/agent-skills",
            json={
                "agent_id": "requirements", "scope": "org", "scope_id": None,
                "skill_key": skill_key, "display_name": "Org Skill", "body": "x",
            },
            headers={"Authorization": f"Bearer {author_token}"},
        )
        assert created.status_code == 200, created.text

        evaluated = await client.post(
            f"/agent-skills/{skill_key}/evaluate",
            json={"agent_id": "requirements", "scope": "org", "scope_id": None},
            headers={"Authorization": f"Bearer {evaluator_token}"},
        )
        assert evaluated.status_code == 201, evaluated.text


@pytest.mark.asyncio
async def test_org_scope_self_evaluation_blocked(mint_token):
    """The mirror negative case of test_evaluate_skill_by_a_different_actor_than_
    the_author_succeeds: an org-scope skill's own author must be refused when they
    call /evaluate on their own draft (R3) — SELF_EVALUATION_BLOCKED, not a pass."""
    tenant = str(uuid.uuid4())
    author_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    await _bind_role(tenant, author_id, "bu_admin", "business_unit", ws_id)
    author_token = mint_token(user_id=author_id, tenant_id=tenant, permissions=["artifact:view"])
    # Random per-run key — see the matching comment in
    # test_evaluate_skill_by_a_different_actor_than_the_author_succeeds above.
    skill_key = f"org-skill-self-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/agent-skills",
            json={
                "agent_id": "requirements", "scope": "org", "scope_id": None,
                "skill_key": skill_key, "display_name": "Org Skill Self", "body": "x",
            },
            headers={"Authorization": f"Bearer {author_token}"},
        )
        assert created.status_code == 200, created.text

        evaluated = await client.post(
            f"/agent-skills/{skill_key}/evaluate",
            json={"agent_id": "requirements", "scope": "org", "scope_id": None},
            headers={"Authorization": f"Bearer {author_token}"},
        )
        assert evaluated.status_code == 403, evaluated.text
        assert evaluated.json()["detail"]["code"] == "SELF_EVALUATION_BLOCKED"
