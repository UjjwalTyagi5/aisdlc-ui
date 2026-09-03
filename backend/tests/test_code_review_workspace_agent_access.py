import uuid as _uuid

import pytest
from fastapi.testclient import TestClient

import process_api
from config.auth.jwt import create_access_token
from shared.authz.grant import grant_role
from shared.db import get_db_session_for_tenant, get_db_session_superuser
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def project_with_two_contributors():
    """A QA/Tester (AGENT_DEFAULT_REACH["code_review"]["qa"] == "none") and an
    Architect (owner) on the same project — the exact distinction
    require_project_access() alone cannot draw."""
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    qa = f"qa-{_uuid.uuid4()}"
    architect = f"arch-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'CRW Test')"
        ), {"i": org, "s": f"crw-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'CRW Project')"
        ), {"i": project, "w": unit, "t": org})
    await grant_role(qa, project, "qa", tenant_id=org, scope_kind="project", granted_by="test")
    await grant_role(architect, project, "architect", tenant_id=org, scope_kind="project", granted_by="test")
    yield {"org": org, "project": project, "qa": qa, "architect": architect}


def _client() -> TestClient:
    return TestClient(process_api.app)


def _hdr(user_id: str, org: str, perms: list[str]) -> dict:
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, tenant_id=org, permissions=perms)
    }


def test_a_qa_project_member_gets_403_on_code_review_despite_project_access(
    project_with_two_contributors,
):
    """QA is a real member of this project (require_project_access() alone would let
    them through) but AGENT_DEFAULT_REACH["code_review"]["qa"] == "none" (PRD §14.7) —
    confirms require_agent_access("code_review") is actually consulted, not just
    project membership."""
    t = project_with_two_contributors
    resp = _client().get(
        f"/code-review/{t['project']}/reviews",
        headers=_hdr(t["qa"], t["org"], ["artifact:view"]),
    )
    assert resp.status_code == 403


def test_the_owning_architect_reaches_the_same_route(project_with_two_contributors):
    t = project_with_two_contributors
    resp = _client().get(
        f"/code-review/{t['project']}/reviews",
        headers=_hdr(t["architect"], t["org"], ["artifact:view"]),
    )
    assert resp.status_code == 200
