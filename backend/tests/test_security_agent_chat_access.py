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


async def test_the_form_user_id_can_no_longer_impersonate_someone_else(project_with_org_admin):
    """The real, authenticated caller is the org_admin (no default security access),
    but the Form `user_id` field names a DIFFERENT, real user who DOES have access via
    a person-level `agent_access_overrides` row.

    This is the discriminating case an earlier version of this test could not prove:
    naming a nonexistent user is denied whether or not the route trusts the Form
    field, since neither identity would have access either way. Here, if the route
    incorrectly trusted the Form field, the request would SUCCEED (the named user has
    an override granting them "security"). Since the route correctly resolves
    identity from the verified session (`request.state.user_id`), it must still 403
    -- the org_admin session holds no override and no default reach, regardless of
    who the form claims to be.
    """
    t = project_with_org_admin
    overridden_user = f"overridden-{_uuid.uuid4()}"

    # agent_access_overrides.user_id carries a real FK to users.id (Task 3,
    # fk_agent_access_override_user) — the row must exist before it can be
    # referenced by an override, same pattern as test_agent_access.py's
    # test_a_person_level_override_grants_access_without_touching_the_role.
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO users (id, tenant_id, email) VALUES (:i, :t, 'overridden-user@example.com')"
        ), {"i": overridden_user, "t": t["org"]})
    async with get_db_session_for_tenant(t["org"]) as s:
        await s.execute(text(
            "INSERT INTO agent_access_overrides "
            "(id, tenant_id, project_id, user_id, phase, involvement) "
            "VALUES (:i, :t, :p, :u, 'security', 'use')"
        ), {"i": str(_uuid.uuid4()), "t": t["org"], "p": t["project"], "u": overridden_user})

    resp = _client().post(
        "/sdlc/agent/security/chat/",
        data={
            "session_id": str(_uuid.uuid4()),
            "user_id": overridden_user,
            "text": "scan this",
            "project_id": t["project"],
        },
        headers=_hdr(t["org_admin"], t["org"], ["admin:*"]),
    )
    assert resp.status_code == 403


async def test_a_role_held_only_on_a_different_project_does_not_reach_this_ones_security_agent():
    """`platform_role_for` resolves a role the caller holds ANYWHERE in the tenant
    (roles_held's own docstring: "any scope") -- so a Developer added to Project A
    must NOT be able to target Project B's chat route and be granted "use" access to
    Security purely because AGENT_DEFAULT_REACH["security"]["developer"] == "use".
    They hold no binding on Project B at all. This is the scenario
    assert_agent_access_for_chat's membership check (visible_project_ids) exists for --
    a plain resolve_project + assert_agent_access pair would incorrectly let this
    through, since neither checks WHICH project the caller's role binding is on.
    """
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project_a = str(_uuid.uuid4())
    project_b = str(_uuid.uuid4())
    dev = f"dev-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Membership Test')"
        ), {"i": org, "s": f"membership-{org[:8]}"})
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
    # dev is a Developer on Project A only -- never added to Project B.
    await grant_role(dev, project_a, "developer", tenant_id=org, scope_kind="project", granted_by="test")

    resp = _client().post(
        "/sdlc/agent/security/chat/",
        data={
            "session_id": str(_uuid.uuid4()),
            "user_id": dev,
            "text": "scan this",
            "project_id": project_b,
        },
        headers=_hdr(dev, org, ["artifact:view"]),
    )
    assert resp.status_code == 404
