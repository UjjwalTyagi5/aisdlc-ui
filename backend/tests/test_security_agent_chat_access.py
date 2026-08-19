# backend/tests/test_security_agent_chat_access.py
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
    # NOTE: AGENT_DEFAULT_REACH["security"] (backend/config/agent_registry.py) grants
    # every one of the 8 Portfolio-1 roles at least "use" reach to the security agent
    # (developer included) -- so a "developer" caller would NOT get a 403 here, despite
    # what an earlier draft of this test assumed. org_admin is not a key in that table
    # at all (Organization Admin holds zero agent access by design -- spec §1.4), so it
    # is the genuine default-deny caller for this route. Same pattern Task 5's
    # test_security_workspace_agent_access.py already established for the same reason.
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    org_admin = f"admin-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Chat Test')"
        ), {"i": org, "s": f"chat-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Chat Project')"
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


def test_an_org_admin_without_security_access_is_refused_before_any_scan_runs(project_with_org_admin):
    t = project_with_org_admin
    resp = _client().post(
        "/sdlc/agent/security/chat/",
        data={
            "session_id": str(_uuid.uuid4()),
            "user_id": t["org_admin"],
            "text": "scan this",
            "project_id": t["project"],
        },
        headers=_hdr(t["org_admin"], t["org"], ["admin:*"]),
    )
    assert resp.status_code == 403


def test_the_form_user_id_can_no_longer_impersonate_someone_else(project_with_org_admin):
    """The real, authenticated caller is the org_admin (no security access) even though
    the Form field claims to be someone else entirely -- proving identity now comes from
    the verified session, not the request body."""
    t = project_with_org_admin
    resp = _client().post(
        "/sdlc/agent/security/chat/",
        data={
            "session_id": str(_uuid.uuid4()),
            "user_id": "someone-else-entirely",
            "text": "scan this",
            "project_id": t["project"],
        },
        headers=_hdr(t["org_admin"], t["org"], ["admin:*"]),
    )
    assert resp.status_code == 403
