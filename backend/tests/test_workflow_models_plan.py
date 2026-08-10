from shared.models.workflow_models import SDLCWorkflowInput
from workflows.execution_plan import build_execution_plan, ExecutionPlan


def test_workflow_input_has_execution_plan_field():
    inp = SDLCWorkflowInput(
        run_id="r1", project_id="p1", tenant_id="t1",
    )
    assert inp.execution_plan is None


def test_workflow_input_with_plan():
    plan = build_execution_plan(run_id="r1", project_id="p1", mode="pipeline")
    inp = SDLCWorkflowInput(
        run_id="r1", project_id="p1", tenant_id="t1",
        execution_plan=plan.model_dump(),
    )
    restored = ExecutionPlan(**inp.execution_plan)
    assert restored.phases[0].agent_ids == ["requirements"]


def test_workflow_input_roundtrip():
    plan = build_execution_plan(run_id="r1", project_id="p1", mode="pipeline")
    inp = SDLCWorkflowInput(
        run_id="r1", project_id="p1", tenant_id="t1",
        execution_plan=plan.model_dump(),
    )
    data = inp.model_dump()
    restored = SDLCWorkflowInput(**data)
    assert restored.execution_plan is not None
    assert restored.execution_plan["phases"][0]["agent_ids"] == ["requirements"]
