"""RBAC coverage for dev_workspace_router — before this task, every route under
/dev/{project_id}/... was gated only by require_project_access() (generic project
membership), with no per-agent check at all. Mirrors
test_security_workspace_agent_access.py's pattern, plus the cross-project case that
file doesn't cover (see docs/superpowers/specs/2026-08-31-development-agent-verification-design.md
Part 2.4)."""
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
    developer = f"dev-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'DevWS Test')"
        ), {"i": org, "s": f"devws-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'DevWS Project')"
        ), {"i": project, "w": unit, "t": org})
    await grant_role(org_admin, org, "org_admin", tenant_id=org, scope_kind="organization", granted_by="test")
    await grant_role(developer, project, "developer", tenant_id=org, scope_kind="project", granted_by="test")
    yield {"org": org, "project": project, "org_admin": org_admin, "developer": developer}


def _client() -> TestClient:
    return TestClient(process_api.app)


def _hdr(user_id: str, org: str, perms: list[str]) -> dict:
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, tenant_id=org, permissions=perms)
    }


def test_an_org_admin_has_no_default_agent_access_and_gets_403_on_workspace_tree(project_with_two_contributors):
    t = project_with_two_contributors
    resp = _client().get(
        f"/dev/{t['project']}/workspace/tree",
        headers=_hdr(t["org_admin"], t["org"], ["admin:*"]),
    )
    assert resp.status_code == 403


def test_the_developer_reaches_the_same_route(project_with_two_contributors):
    t = project_with_two_contributors
    resp = _client().get(
        f"/dev/{t['project']}/workspace/tree",
        headers=_hdr(t["developer"], t["org"], ["artifact:view"]),
    )
    assert resp.status_code == 200


async def test_a_role_held_only_on_a_different_project_does_not_reach_this_ones_dev_workspace():
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project_a = str(_uuid.uuid4())
    project_b = str(_uuid.uuid4())
    dev = f"dev-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'DevWS Membership Test')"
        ), {"i": org, "s": f"devws-mem-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Project A')"
        ), {"i": project_a, "w": unit, "t": org})
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Project B')"
        ), {"i": project_b, "w": unit, "t": org})
    await grant_role(dev, project_a, "developer", tenant_id=org, scope_kind="project", granted_by="test")

    resp = _client().get(
        f"/dev/{project_b}/workspace/tree",
        headers=_hdr(dev, org, ["artifact:view"]),
    )
    assert resp.status_code == 404
