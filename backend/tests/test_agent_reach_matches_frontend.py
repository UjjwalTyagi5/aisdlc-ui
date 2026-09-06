"""The backend's reach table and the frontend's ownership table must agree.

They are two transcriptions of one policy. When they disagree the user is either
shown an agent the API will refuse — a tile that 403s on click — or refused one the
UI offered. Neither failure is visible in either codebase alone, which is why this
reads the TypeScript and compares.
"""
import pathlib
import re

import pytest

from config.agent_registry import AGENT_DEFAULT_REACH

# The frontend Phase for an agent id, where the two vocabularies differ.
_PHASE_FOR_AGENT = {"code_review": "review"}

_ROLES = (
    "project_admin", "ba", "architect", "developer", "qa",
    "security_engineer", "devops_engineer", "scrum_master",
)


def _frontend_ownership() -> dict[str, dict[str, str]]:
    """Parse AGENT_OWNERSHIP out of frontend/lib/roles.ts.

    Deliberately a parse rather than a hand-copied fixture: a fixture is a third
    transcription of the same policy and would drift from both.
    """
    src = (
        pathlib.Path(__file__).resolve().parents[2]
        / "frontend" / "lib" / "roles.ts"
    ).read_text(encoding="utf-8")
    body = src[src.index("export const AGENT_OWNERSHIP"):]
    body = body[: body.index("\n};")]

    out: dict[str, dict[str, str]] = {}
    for role in _ROLES:
        m = re.search(rf"^  {role}: {{(.*?)^  }},", body, re.S | re.M)
        if not m:
            continue
        block = m.group(1)
        if "ALL_OWNER" in block:
            out[role] = {"__all__": "owner"}
            continue
        entries = dict(re.findall(r'(\w+):\s*"(\w+)"', block))
        out[role] = entries
    return out


@pytest.fixture(scope="module")
def frontend():
    return _frontend_ownership()


def test_the_frontend_table_was_found(frontend):
    """A parse that silently matched nothing would make every case below vacuous."""
    assert set(_ROLES) <= set(frontend)
    assert frontend["ba"].get("requirements") == "owner"


@pytest.mark.parametrize("agent", sorted(AGENT_DEFAULT_REACH))
def test_each_agent_reaches_the_same_roles_on_both_sides(agent, frontend):
    phase = _PHASE_FOR_AGENT.get(agent, agent)

    backend_reach = {
        role for role, involvement in AGENT_DEFAULT_REACH[agent].items()
        if involvement != "none"
    }
    frontend_reach = {
        role for role in _ROLES
        if frontend[role].get("__all__") == "owner"
        or frontend[role].get(phase, "none") != "none"
    }

    assert backend_reach == frontend_reach, (
        f"{agent}: backend grants {sorted(backend_reach)}, "
        f"frontend grants {sorted(frontend_reach)}"
    )


def test_every_agent_has_exactly_one_delivery_owner():
    """Two owners would mean the softer 'use' tier came back under another name;
    none would mean only the Project Admin fallback can open it."""
    for agent, reach in AGENT_DEFAULT_REACH.items():
        owners = [
            role for role, involvement in reach.items()
            if involvement != "none" and role != "project_admin"
        ]
        assert len(owners) == 1, f"{agent} has owners {owners}, expected exactly one"


def test_project_admin_reaches_every_agent():
    """The universal fallback approver — a gate with nobody behind it stalls work."""
    for agent, reach in AGENT_DEFAULT_REACH.items():
        assert reach["project_admin"] == "owner", agent


def test_the_governance_tier_is_absent_entirely():
    """Org and BU Admins are governance-only (PRD §14.8). Absent, not 'none' —
    a row for them would invite someone to fill it in."""
    for agent, reach in AGENT_DEFAULT_REACH.items():
        assert "org_admin" not in reach, agent
        assert "bu_admin" not in reach, agent
