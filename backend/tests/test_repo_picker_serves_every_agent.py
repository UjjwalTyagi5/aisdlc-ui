"""The repo picker answers anyone who reaches ANY repo-using agent on the project.

THE LIVE FAILURE (15 Sep 2026). A QA granted the Code Review agent opened its page,
clicked "Select target", and the dialog said "No Azure DevOps projects found" —
Azure DevOps was connected and credentialed. The dialog lists sources, projects,
repos and branches through `/dev/{id}/sources` and `/dev/{id}/ado/...`, which sat on
the Development agent's router behind `require_agent_access("development")`: a 403,
which the dialog rendered as an empty list. The Architect — who OWNS Code Review and
by the one-agent-one-role rule does not reach Development — hit the same wall.

The four browse routes now sit on their own router gated by "reaches any of the
agents whose pages open a target dialog"; the routes that do something (pull a
workspace, read its tree) keep the Development gate.
"""
from __future__ import annotations

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
async def project():
    org, unit, project = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Picker Test')"
        ), {"i": org, "s": f"picker-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Picker Project')"
        ), {"i": project, "w": unit, "t": org})
    return {"org": org, "project": project}


def _hdr(user_id: str, org: str) -> dict:
    return {"Authorization": "Bearer " + create_access_token(
        user_id=user_id, tenant_id=org, permissions=["artifact:view"])}


async def _member(org: str, project: str, role: str, extra_agents: list[str] | None = None) -> str:
    user = f"{role}-{_uuid.uuid4()}"
    await grant_role(user, project, role, tenant_id=org, scope_kind="project", granted_by="test")
    if extra_agents:
        import json
        async with get_db_session_for_tenant(org) as s:
            await s.execute(text(
                "UPDATE role_bindings SET extra_agents = CAST(:a AS jsonb) "
                "WHERE user_id = :u AND scope_kind = 'project' AND scope_id = CAST(:p AS uuid)"
            ), {"a": json.dumps(extra_agents), "u": user, "p": project})
    return user


def _sources(user: str, t: dict) -> int:
    return TestClient(process_api.app).get(
        f"/dev/{t['project']}/sources", headers=_hdr(user, t["org"]),
    ).status_code


async def test_the_architect_who_owns_code_review_reaches_the_picker(project):
    architect = await _member(project["org"], project["project"], "architect")
    assert _sources(architect, project) == 200


async def test_a_qa_granted_code_review_reaches_the_picker(project):
    qa = await _member(project["org"], project["project"], "qa", extra_agents=["review"])
    assert _sources(qa, project) == 200


async def test_a_qa_reaches_it_for_their_own_testing_agent_too(project):
    qa = await _member(project["org"], project["project"], "qa")
    assert _sources(qa, project) == 200, "the Testing page opens a target dialog as well"


async def test_a_scrum_master_reaches_no_repo_agent_and_is_still_refused(project):
    sm = await _member(project["org"], project["project"], "scrum_master")
    assert _sources(sm, project) == 403


async def test_the_workspace_itself_still_needs_the_development_agent(project):
    """Browsing is shared; the workspace is the Development agent's. An Architect
    can pick a target for a review but not read Development's checked-out tree."""
    architect = await _member(project["org"], project["project"], "architect")
    resp = TestClient(process_api.app).get(
        f"/dev/{project['project']}/workspace/tree", headers=_hdr(architect, project["org"]),
    )
    assert resp.status_code == 403


def test_every_target_dialog_agent_is_on_the_picker_gate():
    from shared.routers.dev_workspace import REPO_PICKER_AGENTS

    assert set(REPO_PICKER_AGENTS) == {
        "development", "code_review", "security", "testing", "deployment", "documentation",
    }
