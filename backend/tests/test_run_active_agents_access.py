"""POST /runs did not validate `active_agents` against the caller's per-agent access
before creating the Run row (design doc §4.1/§4.3) — a caller could name an agent they
have no reach to and have it execute anyway, bypassing every router-level
require_agent_access/assert_agent_access gate. Covers the fix in
shared/routers/runs.py::create_run.
"""
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
async def project_with_project_admin_and_org_admin():
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    pa = f"pa-{_uuid.uuid4()}"
    org_admin = f"admin-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Runs Gate Test')"
        ), {"i": org, "s": f"runs-gate-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Runs Gate Project')"
        ), {"i": project, "w": unit, "t": org})
    await grant_role(pa, project, "project_admin", tenant_id=org, scope_kind="project", granted_by="test")
    await grant_role(org_admin, org, "org_admin", tenant_id=org, scope_kind="organization", granted_by="test")
    yield {"org": org, "project": project, "pa": pa, "org_admin": org_admin}


def _client() -> TestClient:
    return TestClient(process_api.app)


def _hdr(user_id: str, org: str, perms: list[str]) -> dict:
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, tenant_id=org, permissions=perms)
    }


def test_an_org_admin_cannot_start_a_run_naming_an_agent_they_have_no_reach_to(
    project_with_project_admin_and_org_admin,
):
    """org_admin holds run:create (via admin:*, the RBAC wildcard) but zero default
    agent access (spec §1.4) — active_agents must still be checked per-agent, not
    waved through because the caller passed the run:create floor."""
    t = project_with_project_admin_and_org_admin
    resp = _client().post(
        "/runs",
        json={"project_id": t["project"], "active_agents": ["security"]},
        headers=_hdr(t["org_admin"], t["org"], ["admin:*"]),
    )
    assert resp.status_code == 403


def test_a_project_admin_can_start_a_run_naming_agents_they_own(
    project_with_project_admin_and_org_admin,
):
    """project_admin is the fallback owner of every agent in-portfolio (spec §1.4) —
    naming any Portfolio-1 agent in active_agents must succeed."""
    t = project_with_project_admin_and_org_admin
    resp = _client().post(
        "/runs",
        json={"project_id": t["project"], "active_agents": ["security", "requirements"]},
        headers=_hdr(t["pa"], t["org"], ["run:create", "artifact:view"]),
    )
    assert resp.status_code == 201


async def test_a_role_held_only_on_a_different_project_cannot_start_a_run_here_either():
    """Same cross-project leakage class as the chat routes: a role held on Project A
    must not reach Project B's agents via active_agents just because the role name
    matches on paper."""
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project_a = str(_uuid.uuid4())
    project_b = str(_uuid.uuid4())
    dev = f"dev-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Runs Gate Membership')"
        ), {"i": org, "s": f"runs-gate-mem-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'A')"
        ), {"i": project_a, "w": unit, "t": org})
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'B')"
        ), {"i": project_b, "w": unit, "t": org})
    await grant_role(dev, project_a, "developer", tenant_id=org, scope_kind="project", granted_by="test")

    resp = _client().post(
        "/runs",
        json={"project_id": project_b, "active_agents": ["security"]},
        headers=_hdr(dev, org, ["run:create", "artifact:view"]),
    )
    assert resp.status_code in (403, 404)
