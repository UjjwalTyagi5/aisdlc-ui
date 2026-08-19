import uuid as _uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org_project_user():
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    user = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Grain Test')"
        ), {"i": org, "s": f"grain-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
        await s.execute(text(
            "INSERT INTO users (id, tenant_id, email) VALUES (:i, :t, 'grain@example.com')"
        ), {"i": user, "t": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Grain Project')"
        ), {"i": project, "w": unit, "t": org})
    yield {"org": org, "project": project, "user": user}


@pytest.mark.asyncio
async def test_a_role_level_override_row_is_accepted(org_project_user):
    t = org_project_user
    async with get_db_session_for_tenant(t["org"]) as db:
        await db.execute(text(
            "INSERT INTO agent_access_overrides "
            "(id, tenant_id, project_id, role, phase, involvement) "
            "VALUES (:i, :t, :p, 'developer', 'security', 'use')"
        ), {"i": str(_uuid.uuid4()), "t": t["org"], "p": t["project"]})
        row = (await db.execute(text(
            "SELECT involvement FROM agent_access_overrides "
            "WHERE project_id = :p AND role = 'developer' AND phase = 'security'"
        ), {"p": t["project"]})).first()
        assert row.involvement == "use"


@pytest.mark.asyncio
async def test_a_person_level_override_row_is_accepted(org_project_user):
    t = org_project_user
    async with get_db_session_for_tenant(t["org"]) as db:
        await db.execute(text(
            "INSERT INTO agent_access_overrides "
            "(id, tenant_id, project_id, user_id, phase, involvement) "
            "VALUES (:i, :t, :p, :u, 'security', 'use')"
        ), {"i": str(_uuid.uuid4()), "t": t["org"], "p": t["project"], "u": t["user"]})
        row = (await db.execute(text(
            "SELECT involvement FROM agent_access_overrides "
            "WHERE project_id = :p AND user_id = :u AND phase = 'security'"
        ), {"p": t["project"], "u": t["user"]})).first()
        assert row.involvement == "use"


@pytest.mark.asyncio
async def test_a_row_with_both_role_and_user_id_is_rejected(org_project_user):
    t = org_project_user
    async with get_db_session_for_tenant(t["org"]) as db:
        with pytest.raises(IntegrityError):
            await db.execute(text(
                "INSERT INTO agent_access_overrides "
                "(id, tenant_id, project_id, role, user_id, phase, involvement) "
                "VALUES (:i, :t, :p, 'developer', :u, 'security', 'use')"
            ), {"i": str(_uuid.uuid4()), "t": t["org"], "p": t["project"], "u": t["user"]})


@pytest.mark.asyncio
async def test_a_row_with_neither_role_nor_user_id_is_rejected(org_project_user):
    t = org_project_user
    async with get_db_session_for_tenant(t["org"]) as db:
        with pytest.raises(IntegrityError):
            await db.execute(text(
                "INSERT INTO agent_access_overrides "
                "(id, tenant_id, project_id, phase, involvement) "
                "VALUES (:i, :t, :p, 'security', 'use')"
            ), {"i": str(_uuid.uuid4()), "t": t["org"], "p": t["project"]})
