import uuid as _uuid

import pytest
from sqlalchemy import text

from shared.authz.agent_access import assert_agent_access, check_agent_access
from shared.db import get_db_session_for_tenant, get_db_session_superuser
from fastapi import HTTPException

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org_project():
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Access Test')"
        ), {"i": org, "s": f"access-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Access Project')"
        ), {"i": project, "w": unit, "t": org})
    yield {"org": org, "project": project}


@pytest.mark.asyncio
async def test_security_engineer_reaches_security_by_default(org_project):
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        allowed = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="security_engineer", user_id=str(_uuid.uuid4()), agent_id="security",
        )
    assert allowed is True


@pytest.mark.asyncio
async def test_developer_does_not_reach_deployment_by_default(org_project):
    # NOTE: AGENT_DEFAULT_REACH (config/agent_registry.py, Task 2 — transcribed
    # verbatim from PRD §14.7 / spec Appendix) gives "developer" a "use" reach to
    # "security" (every delivery role reaches Security at least at "use" by
    # design), so that pairing cannot demonstrate a default-deny. "deployment" is
    # the agent where the spec's own table marks Developer "-" (no default
    # involvement), so it's used here instead to test the same property: a real
    # delivery role, not just an admin role, can be denied by the default table.
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        allowed = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="developer", user_id=str(_uuid.uuid4()), agent_id="deployment",
        )
    assert allowed is False


@pytest.mark.asyncio
async def test_project_admin_reaches_every_portfolio_1_agent(org_project):
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        for agent_id in (
            "requirements", "design", "development", "code_review",
            "security", "testing", "deployment", "documentation",
        ):
            allowed = await check_agent_access(
                db, tenant_id=t["org"], project_id=t["project"],
                role="project_admin", user_id=str(_uuid.uuid4()), agent_id=agent_id,
            )
            assert allowed is True, agent_id


@pytest.mark.asyncio
async def test_org_admin_permissions_do_not_grant_agent_access(org_project):
    """admin:* is never consulted here — org_admin holds zero agent access by design."""
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        allowed = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="org_admin", user_id=str(_uuid.uuid4()), agent_id="security",
        )
    assert allowed is False


@pytest.mark.asyncio
async def test_a_role_level_override_grants_access_the_default_table_denies(org_project):
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        await db.execute(text(
            "INSERT INTO agent_access_overrides "
            "(id, tenant_id, project_id, role, phase, involvement) "
            "VALUES (:i, :t, :p, 'developer', 'deployment', 'use')"
        ), {"i": str(_uuid.uuid4()), "t": t["org"], "p": t["project"]})
        allowed = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="developer", user_id=str(_uuid.uuid4()), agent_id="deployment",
        )
    assert allowed is True


@pytest.mark.asyncio
async def test_a_person_level_override_grants_access_without_touching_the_role(org_project):
    t = org_project
    other_developer = str(_uuid.uuid4())
    named_developer = str(_uuid.uuid4())
    # agent_access_overrides.user_id carries a real FK to users.id (Task 3,
    # fk_agent_access_override_user) — the row must exist before it can be
    # referenced by an override.
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO users (id, tenant_id, email) VALUES (:i, :t, 'named-developer@example.com')"
        ), {"i": named_developer, "t": t["org"]})
    async with get_db_session_for_tenant(t["org"]) as db:
        await db.execute(text(
            "INSERT INTO agent_access_overrides "
            "(id, tenant_id, project_id, user_id, phase, involvement) "
            "VALUES (:i, :t, :p, :u, 'deployment', 'use')"
        ), {"i": str(_uuid.uuid4()), "t": t["org"], "p": t["project"], "u": named_developer})

        allowed_named = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="developer", user_id=named_developer, agent_id="deployment",
        )
        allowed_other = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="developer", user_id=other_developer, agent_id="deployment",
        )
    assert allowed_named is True
    assert allowed_other is False


@pytest.mark.asyncio
async def test_assert_agent_access_raises_403_on_denial(org_project):
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        with pytest.raises(HTTPException) as exc:
            await assert_agent_access(
                db, tenant_id=t["org"], project_id=t["project"],
                role="developer", user_id=str(_uuid.uuid4()), agent_id="deployment",
            )
    assert exc.value.status_code == 403
