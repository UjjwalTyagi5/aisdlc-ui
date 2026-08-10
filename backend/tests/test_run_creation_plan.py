"""Tests for execution plan wiring in run creation (Task 6).

Verifies:
  1. RunCreateIn schema accepts active_agents / skip_agents (optional).
  2. build_execution_plan is called and its output is passed to SDLCWorkflowInput.
"""
from shared.routers._schemas import RunCreateIn
from workflows.execution_plan import build_execution_plan


def test_run_create_in_has_agent_fields():
    body = RunCreateIn(project_id="p1", trigger="manual")
    assert body.active_agents is None
    assert body.skip_agents is None


def test_run_create_in_with_agents():
    body = RunCreateIn(
        project_id="p1", trigger="manual",
        active_agents=["requirements", "design"],
        skip_agents=["deployment"],
    )
    assert body.active_agents == ["requirements", "design"]
    assert body.skip_agents == ["deployment"]


def test_plan_passed_to_workflow_input():
    """build_execution_plan output can be passed as execution_plan dict to SDLCWorkflowInput."""
    from shared.models.workflow_models import SDLCWorkflowInput

    plan = build_execution_plan(
        run_id="r1",
        project_id="p1",
        mode="pipeline",
        active_agents=["requirements", "design"],
        skip_agents=None,
    )
    inp = SDLCWorkflowInput(
        run_id="r1",
        project_id="p1",
        tenant_id="t1",
        trigger="manual",
        execution_plan=plan.model_dump(),
    )
    assert inp.execution_plan is not None
    assert inp.execution_plan["run_id"] == "r1"
    assert len(inp.execution_plan["phases"]) > 0
