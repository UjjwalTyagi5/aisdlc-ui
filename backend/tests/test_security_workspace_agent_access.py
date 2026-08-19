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
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    org_admin = f"admin-{_uuid.uuid4()}"
    security_eng = f"sec-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'SW Test')"
        ), {"i": org, "s": f"sw-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'SW Project')"
        ), {"i": project, "w": unit, "t": org})
    await grant_role(org_admin, org, "org_admin", tenant_id=org, scope_kind="organization", granted_by="test")
    await grant_role(security_eng, project, "security_engineer", tenant_id=org, scope_kind="project", granted_by="test")
    yield {"org": org, "project": project, "org_admin": org_admin, "security_eng": security_eng}


def _client() -> TestClient:
    return TestClient(process_api.app)


def _hdr(user_id: str, org: str, perms: list[str]) -> dict:
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, tenant_id=org, permissions=perms)
    }


def test_an_org_admin_has_no_default_agent_access_and_gets_403_on_security_scans(project_with_two_contributors):
    """org_admin holds admin:* but that permission is never consulted for agent
    access (spec §1.4) — confirms the property end-to-end through a real router,
    not just check_agent_access() in isolation (that's Task 4's job)."""
    t = project_with_two_contributors
    resp = _client().get(
        f"/security/{t['project']}/scans",
        headers=_hdr(t["org_admin"], t["org"], ["admin:*"]),
    )
    assert resp.status_code == 403


def test_the_security_engineer_reaches_the_same_route(project_with_two_contributors):
    t = project_with_two_contributors
    resp = _client().get(
        f"/security/{t['project']}/scans",
        headers=_hdr(t["security_eng"], t["org"], ["artifact:view"]),
    )
    assert resp.status_code == 200
