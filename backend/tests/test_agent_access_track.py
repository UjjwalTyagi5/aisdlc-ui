"""The standalone gate for Track 3's agents: agent reach AND the project's track.

Against the real test database, with real role bindings — the same shape as
tests/test_agent_access.py — because the function under test resolves project,
membership and role from those tables.
"""
from __future__ import annotations

import uuid as _uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from shared.authz.agent_access import (
    assert_agent_access_for_chat,
    assert_agent_access_for_chat_on_track,
)
from shared.authz.grant import grant_role
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org_with_tracks():
    """One organization, one Business Unit, one Code Modernization project and one
    Greenfield project."""
    org, unit = str(_uuid.uuid4()), str(_uuid.uuid4())
    modern, green = str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Track Test')"
        ), {"i": org, "s": f"track-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        for pid, name, track in ((modern, "Legacy Billing", "modernization"),
                                 (green, "Shiny New App", "greenfield")):
            await s.execute(text(
                "INSERT INTO projects (id, workspace_id, tenant_id, display_name, track) "
                "VALUES (:i, :w, :t, :n, :tr)"
            ), {"i": pid, "w": unit, "t": org, "n": name, "tr": track})
    yield {"org": org, "modern": modern, "green": green}


async def _member(role: str, t: dict, *projects: str) -> str:
    user = f"{role}-{_uuid.uuid4()}"
    for project in projects:
        await grant_role(user, t[project], role, tenant_id=t["org"], scope_kind="project")
    return user


async def _gate(t: dict, user: str, project: str, agent: str) -> str:
    async with get_db_session_for_tenant(t["org"]) as db:
        return await assert_agent_access_for_chat_on_track(
            db, tenant_id=t["org"], project_id=t[project], user_id=user, agent_id=agent,
        )


async def test_the_ba_reaches_migration_intent_requirements_on_a_modernization_project(org_with_tracks):
    t = org_with_tracks
    user = await _member("ba", t, "modern")
    assert await _gate(t, user, "modern", "requirements_modernization") == t["modern"]


async def test_the_ba_reaches_discovery_on_a_modernization_project(org_with_tracks):
    """Track 3's product decision: the BA owns both of its first two agents."""
    t = org_with_tracks
    user = await _member("ba", t, "modern")
    assert await _gate(t, user, "modern", "discovery") == t["modern"]


async def test_a_greenfield_project_refuses_a_track3_agent_and_says_why(org_with_tracks):
    t = org_with_tracks
    user = await _member("ba", t, "green")
    with pytest.raises(HTTPException) as exc:
        await _gate(t, user, "green", "requirements_modernization")
    assert exc.value.status_code == 403
    assert "not part of this project's delivery track" in exc.value.detail


async def test_a_modernization_project_refuses_portfolio_1_requirements(org_with_tracks):
    """Each track owns its own agents: Track 3's BA uses Track 3's Requirements."""
    t = org_with_tracks
    user = await _member("ba", t, "modern")
    with pytest.raises(HTTPException) as exc:
        await _gate(t, user, "modern", "requirements")
    assert exc.value.status_code == 403


async def test_a_role_that_does_not_own_the_agent_is_refused_on_the_right_track(org_with_tracks):
    """Track membership is necessary, not sufficient — one agent, one role still holds."""
    t = org_with_tracks
    user = await _member("architect", t, "modern")  # owned Discovery in the design doc; not here
    with pytest.raises(HTTPException) as exc:
        await _gate(t, user, "modern", "discovery")
    assert exc.value.status_code == 403
    assert "delivery track" not in exc.value.detail


async def test_the_project_admin_reaches_both(org_with_tracks):
    t = org_with_tracks
    user = await _member("project_admin", t, "modern")
    assert await _gate(t, user, "modern", "requirements_modernization") == t["modern"]
    assert await _gate(t, user, "modern", "discovery") == t["modern"]


async def test_a_non_member_gets_not_found(org_with_tracks):
    t = org_with_tracks
    user = await _member("architect", t, "green")  # a member of the OTHER project only
    with pytest.raises(HTTPException) as exc:
        await _gate(t, user, "modern", "discovery")
    assert exc.value.status_code == 404


async def test_portfolio_1_handlers_are_unchanged_by_track(org_with_tracks):
    """The existing gate still ignores track — the nine standalone handlers keep their
    exact behaviour; only Track 3's two call the track-aware wrapper."""
    t = org_with_tracks
    user = await _member("ba", t, "modern")
    async with get_db_session_for_tenant(t["org"]) as db:
        assert await assert_agent_access_for_chat(
            db, tenant_id=t["org"], project_id=t["modern"], user_id=user,
            agent_id="requirements",
        ) == t["modern"]
