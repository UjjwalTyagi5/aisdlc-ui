# platform/backend/tests/temporal/test_execution_plan.py
import pytest
from workflows.execution_plan import (
    ExecutionPhase,
    ExecutionPlan,
    build_execution_plan,
)


def test_default_pipeline_plan():
    plan = build_execution_plan(
        run_id="run-001",
        project_id="proj-001",
        mode="pipeline",
    )
    assert isinstance(plan, ExecutionPlan)
    assert plan.mode == "pipeline"
    assert len(plan.phases) == 5
    assert plan.phases[0].agent_ids == ["requirements"]
    assert plan.phases[0].gate_type == "approval_required"
    assert plan.phases[0].sla_hours == 24


def test_skip_agents():
    plan = build_execution_plan(
        run_id="run-002",
        project_id="proj-001",
        mode="pipeline",
        skip_agents=["deployment"],
    )
    all_agents = [aid for p in plan.phases for aid in p.agent_ids]
    assert "deployment" not in all_agents
    assert "requirements" in all_agents


def test_active_agents_filter():
    plan = build_execution_plan(
        run_id="run-003",
        project_id="proj-001",
        mode="pipeline",
        active_agents=["requirements", "design"],
    )
    all_agents = [aid for p in plan.phases for aid in p.agent_ids]
    assert set(all_agents) == {"requirements", "design"}


def test_standalone_mode():
    plan = build_execution_plan(
        run_id="run-004",
        project_id="proj-001",
        mode="standalone",
        active_agents=["testing"],
    )
    assert plan.mode == "standalone"
    assert len(plan.phases) == 1
    assert plan.phases[0].agent_ids == ["testing"]


def test_phases_are_sorted_by_position():
    plan = build_execution_plan(
        run_id="run-005",
        project_id="proj-001",
        mode="pipeline",
    )
    positions = []
    for phase in plan.phases:
        for aid in phase.agent_ids:
            from config.agent_registry import AGENT_REGISTRY
            positions.append(AGENT_REGISTRY[aid].pipeline_position)
    assert positions == sorted(positions)


def test_plan_serialization_roundtrip():
    plan = build_execution_plan(run_id="run-006", project_id="proj-001", mode="pipeline")
    data = plan.model_dump()
    restored = ExecutionPlan(**data)
    assert restored.phases[0].agent_ids == plan.phases[0].agent_ids
