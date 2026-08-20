# backend/tests/test_code_review_agent_chat_access.py
"""Mirrors test_security_agent_chat_access.py for the Code Review agent's REST
/chat/ route. Unlike Security's chat() route, Code Review's has no project_id Form
field -- the review target (and its project) is bound out-of-band by
POST /review/prepare into the in-memory session, so these tests pre-bind the
session's project_id directly via get_session(), the same state the real prepare
route would have set.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient

import process_api
from agents_orchestrator.code_review_agent.config.session_state import get_session
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
    # AGENT_DEFAULT_REACH["code_review"] (backend/config/agent_registry.py) grants
    # project_admin/ba/architect/security_engineer at least some reach, and developer
    # "requests" -- but org_admin is not a key in that table at all (Organization
    # Admin holds zero agent access by design -- spec §1.4), so it is the genuine
    # default-deny caller for this route.
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    org_admin = f"admin-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'CR Chat Test')"
        ), {"i": org, "s": f"cr-chat-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'CR Chat Project')"
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


def test_an_org_admin_without_code_review_access_is_refused_before_any_review_runs(project_with_org_admin):
    t = project_with_org_admin
    session_id = str(_uuid.uuid4())
    get_session(session_id).project_id = t["project"]  # simulates a prior /review/prepare

    resp = _client().post(
        "/sdlc/agent/code-review/chat/",
        data={
            "session_id": session_id,
            "user_id": t["org_admin"],
            "text": "review this",
        },
        headers=_hdr(t["org_admin"], t["org"], ["admin:*"]),
    )
    assert resp.status_code == 403


async def test_the_form_user_id_can_no_longer_impersonate_someone_else(project_with_org_admin):
    """The real, authenticated caller is the org_admin (no default code_review
    access), but the Form `user_id` field names a DIFFERENT, real user who DOES have
    access via a person-level `agent_access_overrides` row. If the route incorrectly
    trusted the Form field, the request would SUCCEED. Since it correctly resolves
    identity from the verified session (`request.state.user_id`), it must still 403.
    """
    t = project_with_org_admin
    overridden_user = f"overridden-{_uuid.uuid4()}"

    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO users (id, tenant_id, email) VALUES (:i, :t, 'overridden-user-cr@example.com')"
        ), {"i": overridden_user, "t": t["org"]})
    async with get_db_session_for_tenant(t["org"]) as s:
        await s.execute(text(
            "INSERT INTO agent_access_overrides "
            "(id, tenant_id, project_id, user_id, phase, involvement) "
            "VALUES (:i, :t, :p, :u, 'code_review', 'use')"
        ), {"i": str(_uuid.uuid4()), "t": t["org"], "p": t["project"], "u": overridden_user})

    session_id = str(_uuid.uuid4())
    get_session(session_id).project_id = t["project"]

    resp = _client().post(
        "/sdlc/agent/code-review/chat/",
        data={
            "session_id": session_id,
            "user_id": overridden_user,
            "text": "review this",
        },
        headers=_hdr(t["org_admin"], t["org"], ["admin:*"]),
    )
    assert resp.status_code == 403


async def test_a_role_held_only_on_a_different_project_does_not_reach_this_ones_code_review_agent():
    """A Developer added to Project A must NOT be able to target Project B's chat
    route (via a session pre-bound to Project B) and be granted access purely
    because they hold "developer" somewhere in the tenant -- they hold no binding
    on Project B at all."""
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project_a = str(_uuid.uuid4())
    project_b = str(_uuid.uuid4())
    dev = f"dev-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'CR Membership Test')"
        ), {"i": org, "s": f"cr-membership-{org[:8]}"})
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

    session_id = str(_uuid.uuid4())
    get_session(session_id).project_id = project_b

    resp = _client().post(
        "/sdlc/agent/code-review/chat/",
        data={
            "session_id": session_id,
            "user_id": dev,
            "text": "review this",
        },
        headers=_hdr(dev, org, ["artifact:view"]),
    )
    assert resp.status_code == 404
