"""The Project Manager can see its team, and reports what it actually knows.

WHY THESE TOOLS EXIST. Every other PM tool reads the board — sprints, capacity, work
items. The board knows a display name on a ticket and nothing else about a person: not
that they are this project's Architect, not that they were granted the Security agent
last week, not that they approved the design document yesterday. That lives in
`role_bindings` and `audit_events`, and nothing reached it.

WHAT THE TESTS ARE ACTUALLY GUARDING. The interesting failures here are not "does it
return rows" — they are the ways a team summary becomes fiction:

  · merging platform activity and board tickets into one number, so a person who works
    entirely on the board reads as idle;
  · treating a tenant-wide audit trail as this project's, so somebody's work elsewhere
    is reported as work here;
  · answering "what are their skills" from anything other than the role they hold and
    the work they did, because this platform stores neither a skills table nor a rating.
"""
from __future__ import annotations

import inspect
import sys
import uuid as _uuid
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.db import get_db_session_for_tenant, get_db_session_superuser  # noqa: E402
from shared.tools import project_team as pt  # noqa: E402

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def project():
    org, bu, proj = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Team')"
        ), {"i": org, "s": f"team-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"), {"i": bu, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Team Project')"), {"i": proj, "w": bu, "t": org})

    yield {"org": org, "bu": bu, "proj": proj}

    async with get_db_session_for_tenant(org) as s:
        await s.execute(text("DELETE FROM role_bindings"))
        await s.execute(text("DELETE FROM projects"))
    async with get_db_session_superuser() as s:
        await s.execute(text("DELETE FROM users WHERE tenant_id = CAST(:t AS uuid)"), {"t": org})
        await s.execute(text(
            "DELETE FROM workspaces WHERE organization_id = CAST(:t AS uuid)"), {"t": org})
        await s.execute(text(
            "DELETE FROM organizations WHERE id = CAST(:t AS uuid)"), {"t": org})


async def _member(project, email: str, role: str, extra: str = "[]") -> str:
    uid = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO users (id, tenant_id, email, active) "
            "VALUES (:i, CAST(:t AS uuid), :e, true)"
        ), {"i": uid, "t": project["org"], "e": email})
    async with get_db_session_for_tenant(project["org"]) as s:
        await s.execute(text(
            "INSERT INTO role_bindings (id, tenant_id, user_id, role_name, scope_kind, "
            "  scope_id, status, extra_agents) "
            "VALUES (gen_random_uuid(), CAST(:t AS uuid), :u, :r, 'project', "
            "  CAST(:p AS uuid), 'active', CAST(:x AS jsonb))"
        ), {"t": project["org"], "u": uid, "r": role, "p": project["proj"], "x": extra})
    return uid


# -- the roster ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_roster_names_each_person_and_their_role(project):
    """THE THING THE BOARD CANNOT ANSWER."""
    await _member(project, "ana@abcbank.com", "architect")
    await _member(project, "sam@abcbank.com", "qa")

    rows = await pt._members(project["org"], project["proj"])

    assert {(r["name"], r["role"]) for r in rows} == {
        ("ana@abcbank.com", "architect"), ("sam@abcbank.com", "qa")
    }


@pytest.mark.asyncio
async def test_extra_agents_come_back_as_a_real_list(project):
    """A grant made to one person, read back intact.

    This asserts the OUTPUT shape, not the decode: through SQLAlchemy's asyncpg dialect
    `jsonb` already arrives as a list, verified against the live database, so the
    tolerant `isinstance(str)` branch in `_members` never fires on this path. An earlier
    version of this test claimed to prove that branch and did not — removing the decode
    left it green. It is kept in the code for raw-asyncpg callers and is deliberately
    NOT claimed here."""
    await _member(project, "dev@abcbank.com", "developer", extra='["security"]')

    rows = await pt._members(project["org"], project["proj"])

    assert rows[0]["extra_agents"] == ["security"]
    assert isinstance(rows[0]["extra_agents"], list)


def test_agent_reach_is_read_from_the_platforms_own_matrix():
    """A second copy of the ownership matrix would drift, and nobody asking "what can
    they touch" would ever catch it."""
    from shared.authz.agent_access import AGENT_DEFAULT_REACH

    arch = pt._agents_for("architect", [])

    assert arch["owns"], "the Architect owns at least one agent"
    for agent in arch["owns"]:
        assert AGENT_DEFAULT_REACH[agent]["architect"] in ("owner", "primary")
    # A role with no reach gets empty lists rather than an invented default.
    assert pt._agents_for("contributor", []) == {"owns": [], "uses": [], "granted": []}


def test_an_individually_granted_agent_is_reported_apart_from_the_role():
    """"Granted" is a different fact from "their role owns it" — the first is a decision
    somebody made about this person, and flattening the two hides it."""
    out = pt._agents_for("qa", ["security"])

    assert "security" in out["granted"]
    assert "security" not in out["owns"]


# -- naming a person the way people do ----------------------------------------


@pytest.mark.parametrize("who", ["ana", "ANA", "ana@abcbank.com", "Ana@AbcBank.com"])
def test_a_member_is_found_by_how_people_actually_refer_to_them(who):
    """Nobody types a UUID, and few type the full address. An exact-email match would
    refuse almost every real question."""
    m = {"user_id": "u-1", "name": "ana@abcbank.com"}

    assert pt._matches(m, who) is True


def test_an_empty_query_means_everybody():
    """`member_activity()` with no argument is "how is the team doing"."""
    assert pt._matches({"user_id": "u-1", "name": "ana@abcbank.com"}, "") is True


def test_somebody_elses_name_does_not_match():
    """NON-VACUITY: the substring fallback must not match everyone."""
    assert pt._matches({"user_id": "u-1", "name": "ana@abcbank.com"}, "sam") is False


# -- the honesty constraints --------------------------------------------------


def test_the_two_sources_are_reported_separately():
    """THE FAILURE THIS EXISTS TO PREVENT. A person with no platform activity and four
    tickets in progress is working — on the board. Merging the two into one count makes
    them read as idle, and the number would be indefensible either way."""
    src = inspect.getsource(pt.make_team_tools)

    assert '"platform"' in src and '"board"' in src
    assert "keep them apart" in src.lower() or "SEPARATELY" in src


def test_the_activity_query_is_scoped_to_this_project():
    """`audit_events` is TENANT-wide. Unscoped, a Project Manager asking about their own
    team would be shown work those people did on somebody else's project."""
    src = inspect.getsource(pt._platform_activity)

    assert "project_id" in src
    assert "row_project != project_id" in src


def test_the_tool_refuses_to_invent_a_skills_record():
    """Asked "what are their skills", the only honest sources are the role they hold and
    the work they did. There is no skills table and no rating in this platform, and a
    confident judgement about a colleague assembled from an event count is the worst
    thing this tool could produce."""
    src = inspect.getsource(pt.make_team_tools)

    assert "no skills table" in src
    assert "not recorded" in src


def test_the_prompt_carries_the_same_constraint():
    """The tool docstring is only half of it — the model reads the system prompt first,
    and a prompt that says "summarise how the team is doing" with no caveat is where an
    invented performance review comes from."""
    from agents_orchestrator.pm_agent.agents.schedule import PM_SYS_MESSAGE

    assert "list_project_members" in PM_SYS_MESSAGE
    assert "member_activity" in PM_SYS_MESSAGE
    assert "Do not grade people" in PM_SYS_MESSAGE


def test_the_pm_agent_binds_them():
    """Written is not wired."""
    from agents_orchestrator.pm_agent.agents.schedule import tools

    names = {getattr(t, "name", "") for t in tools}
    assert {"list_project_members", "member_activity"} <= names


def test_neither_tool_takes_its_identity_from_the_model():
    """Same rule as every other tool factory here: the stage is bound at registration,
    so a prompt cannot claim to be a different agent or another project."""
    for t in pt.make_team_tools("plan"):
        exposed = set(getattr(t, "args", {}) or {})
        assert not (exposed & {"stage", "tenant_id", "project_id", "agent_id"})
