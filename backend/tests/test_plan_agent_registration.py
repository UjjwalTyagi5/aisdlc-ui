"""The PM agent is a real stage, wired the same way every other stage is.

PHASE 1. An agent is not "added" by writing its tools — it is added by appearing in the
registry, owning a column, holding a gate, and being reachable by a role. Miss one and
it half-exists: a chat that produces output nothing reads, or a gate nobody can pass.

WHERE IT SITS. Between design and development, because a plan needs a design to size and
Development needs the plan to know what was committed to. Inserting there shifts every
later position, which is safe only because `pipeline_position` is in-code and appears in
no migration — checked before renumbering, not assumed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.agent_registry import (  # noqa: E402
    AGENT_DEFAULT_REACH,
    AGENT_REGISTRY,
    TRACK_PORTFOLIOS,
)


# -- position ------------------------------------------------------------------


@pytest.mark.unit
def test_the_planner_runs_after_design_and_before_development():
    r = AGENT_REGISTRY
    assert r["design"].pipeline_position < r["plan"].pipeline_position
    assert r["plan"].pipeline_position < r["development"].pipeline_position


@pytest.mark.unit
def test_the_parallel_group_stayed_parallel():
    """code_review, security and testing share a position — that is how the registry
    expresses "these run together". Renumbering must move them as a block."""
    positions = {AGENT_REGISTRY[a].pipeline_position for a in ("code_review", "security", "testing")}
    assert len(positions) == 1


@pytest.mark.unit
def test_the_portfolio_order_matches_the_positions():
    """`_PORTFOLIO_1` is a separate ordered list, so it can drift from the positions
    silently — the two are only consistent because both were edited."""
    order = TRACK_PORTFOLIOS["greenfield"]
    positions = [AGENT_REGISTRY[a].pipeline_position for a in order]
    assert positions == sorted(positions)
    assert order.index("plan") == order.index("design") + 1


# -- artifacts -----------------------------------------------------------------


@pytest.mark.unit
def test_it_reads_both_upstream_artifacts():
    """Requirements alone gives scope with no sizing; design alone gives components with
    nothing tying them to what anybody asked for."""
    assert set(AGENT_REGISTRY["plan"].input_artifacts) == {
        "requirements_payload", "design_artifacts"
    }


@pytest.mark.unit
def test_development_actually_reads_the_plan():
    """THE POINT OF THE AGENT. Without a consumer it produces a schedule nothing reads."""
    assert "plan_artifacts" in AGENT_REGISTRY["development"].input_artifacts


@pytest.mark.unit
def test_the_output_column_exists_on_both_artifact_stores():
    """`runs` is the project-scoped record every listing reads; `agent_sessions` is what
    `build_context` resolves for an orchestrated run. Adding to one and not the other is
    exactly how the Requirements chat ended up writing a payload nothing consulted."""
    from shared.models.orm import AgentSession, Run

    assert hasattr(Run, "plan_artifacts")
    assert hasattr(AgentSession, "plan_artifacts")


@pytest.mark.unit
def test_the_stage_maps_to_its_column():
    from shared.services.artifact_service import _COLUMN_MAP

    assert _COLUMN_MAP["plan"] == "plan_artifacts"


@pytest.mark.unit
def test_activity_derives_a_step_from_it():
    """Every other stage shows "<Stage> stage completed" once its column is populated;
    a planner missing from that list would run and leave no trace."""
    from shared.routers._schemas import _JSONB_COLUMNS

    assert ("plan", "plan_artifacts") in _JSONB_COLUMNS


# -- the gate ------------------------------------------------------------------


@pytest.mark.unit
def test_a_plan_needs_signing_off():
    """It commits people and dates. That is a decision somebody makes, not an output
    that simply appears."""
    assert AGENT_REGISTRY["plan"].gate_type == "approval_required"


@pytest.mark.unit
def test_the_phase_resolves_to_its_own_permission():
    from shared.authz.permissions import _PHASE_PERMISSION

    assert _PHASE_PERMISSION["plan"] == "artifact:approve_plan"


@pytest.mark.unit
def test_the_permission_is_in_the_catalogue():
    """A permission nobody catalogued is refused for everyone, silently — the exact
    failure `project:update` had before migration 0020."""
    from shared.authz.permissions import _PERMISSION_CATALOG

    assert "artifact:approve_plan" in _PERMISSION_CATALOG


@pytest.mark.unit
def test_only_the_owning_roles_hold_the_gate():
    from shared.authz.permissions import _ROLE_PERMISSIONS

    holders = {r for r, p in _ROLE_PERMISSIONS.items() if "artifact:approve_plan" in p}
    assert holders == {"project_admin", "scrum_master"}


@pytest.mark.unit
def test_the_owner_can_reach_the_agent_it_signs_off():
    """An owner who cannot open the agent they approve for is a gate with nobody behind
    it — the rule roles.ts states explicitly."""
    reach = AGENT_DEFAULT_REACH["plan"]
    assert reach["scrum_master"] == "owner"
    assert reach["project_admin"] == "owner"


@pytest.mark.unit
def test_scrum_master_finally_owns_something():
    """It was "use" on every agent and owner of none. If this regresses, the role is
    back to having no job."""
    owned = [a for a, reach in AGENT_DEFAULT_REACH.items() if reach.get("scrum_master") == "owner"]
    assert owned == ["plan"]


# -- capabilities --------------------------------------------------------------


@pytest.mark.unit
def test_capacity_is_optional_not_required():
    """ADO exposes capacity; Jira has no capacity API at all. Requiring it would make
    the agent unusable on Jira rather than merely less precise."""
    plan = AGENT_REGISTRY["plan"]
    assert "board.capacity.read" in plan.optional_capabilities
    assert "board.capacity.read" not in plan.required_capabilities


@pytest.mark.unit
def test_the_things_it_cannot_work_without_are_required():
    plan = AGENT_REGISTRY["plan"]
    for capability in ("plan.wbs.generate", "plan.estimate", "plan.schedule.build", "board.read"):
        assert capability in plan.required_capabilities
