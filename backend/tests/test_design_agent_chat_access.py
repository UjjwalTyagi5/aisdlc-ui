# backend/tests/test_design_agent_chat_access.py
"""Portfolio-1 access-hardening pass for the Design agent — mirrors
test_security_agent_chat_access.py's three cases against
/sdlc/agent/design/chat/. AGENT_DEFAULT_REACH["design"] makes "architect" the
owner and does not list "org_admin" at all (zero default agent access, spec
§1.4), so org_admin is the default-deny caller used throughout.
"""
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
async def project_with_org_admin():
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    org_admin = f"admin-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Design Chat Test')"
        ), {"i": org, "s": f"design-chat-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Design Chat Project')"
        ), {"i": project, "w": unit, "t": org})
    await grant_role(org_admin, org, "org_admin", tenant_id=org, scope_kind="organization", granted_by="test")
    yield {"org": org, "project": project, "org_admin": org_admin}


def _client() -> TestClient:
    return TestClient(process_api.app)


def _hdr(user_id: str, org: str, perms: list[str]) -> dict:
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, tenant_id=org, permissions=perms)
    }


def test_an_org_admin_without_design_access_is_refused_before_any_work_runs(project_with_org_admin):
    t = project_with_org_admin
    resp = _client().post(
        "/sdlc/agent/design/chat/",
        data={
            "session_id": str(_uuid.uuid4()),
            "user_id": t["org_admin"],
            "project_id": t["project"],
            "task_intent": "draft the architecture",
        },
        headers=_hdr(t["org_admin"], t["org"], ["admin:*"]),
    )
    assert resp.status_code == 403


async def test_the_form_user_id_can_no_longer_impersonate_someone_else(project_with_org_admin):
    """The real, authenticated caller is the org_admin (no default design access),
    but the Form `user_id` field names a DIFFERENT, real user who DOES have access
    via a person-level `agent_access_overrides` row. If the route incorrectly
    trusted the Form field, this would succeed; since identity comes from the
    verified session, it must still 403."""
    t = project_with_org_admin
    overridden_user = f"overridden-{_uuid.uuid4()}"

    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO users (id, tenant_id, email) VALUES (:i, :t, 'overridden-design@example.com')"
        ), {"i": overridden_user, "t": t["org"]})
    async with get_db_session_for_tenant(t["org"]) as s:
        await s.execute(text(
            "INSERT INTO agent_access_overrides "
            "(id, tenant_id, project_id, user_id, phase, involvement) "
            "VALUES (:i, :t, :p, :u, 'design', 'use')"
        ), {"i": str(_uuid.uuid4()), "t": t["org"], "p": t["project"], "u": overridden_user})

    resp = _client().post(
        "/sdlc/agent/design/chat/",
        data={
            "session_id": str(_uuid.uuid4()),
            "user_id": overridden_user,
            "project_id": t["project"],
            "task_intent": "draft the architecture",
        },
        headers=_hdr(t["org_admin"], t["org"], ["admin:*"]),
    )
    assert resp.status_code == 403


async def test_a_role_held_only_on_a_different_project_does_not_reach_this_ones_design_agent():
    """A Developer added to Project A must not reach Project B's Design agent just
    because AGENT_DEFAULT_REACH["design"] happens to grant some role reach on
    paper -- they hold no binding on Project B at all. (Developer is actually
    "none" for design, so use "security_engineer", which is "use" -- the point is
    proving membership is checked independent of role reach.)"""
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project_a = str(_uuid.uuid4())
    project_b = str(_uuid.uuid4())
    sec_eng = f"sec-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Design Membership Test')"
        ), {"i": org, "s": f"design-membership-{org[:8]}"})
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
    await grant_role(sec_eng, project_a, "security_engineer", tenant_id=org, scope_kind="project", granted_by="test")

    resp = _client().post(
        "/sdlc/agent/design/chat/",
        data={
            "session_id": str(_uuid.uuid4()),
            "user_id": sec_eng,
            "project_id": project_b,
            "task_intent": "draft the architecture",
        },
        headers=_hdr(sec_eng, org, ["artifact:view"]),
    )
    assert resp.status_code == 404
