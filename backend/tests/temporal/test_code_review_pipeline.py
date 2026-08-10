"""Integration tests: Code Review Agent in the data-driven workflow.

Proves the code_review agent works in:
  1. Pipeline mode (after development, before/with testing)
  2. Standalone mode (single-agent plan)
  3. Zero workflow code changes required
"""
import pytest
import uuid

try:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    from temporalio import activity
    _TEMPORALIO_AVAILABLE = True
except ImportError:
    _TEMPORALIO_AVAILABLE = False

_skip = pytest.mark.skipif(not _TEMPORALIO_AVAILABLE, reason="temporalio not installed")

from shared.models.workflow_models import SDLCWorkflowInput, HITLSignal
from shared.models.artifacts import (
    RequirementsArtifact, DesignArtifact, DevelopmentArtifact,
    TestingArtifact, CodeReviewArtifact,
)
from workflows.execution_plan import build_execution_plan


# -- Stub activities ----------------------------------------------------------

@activity.defn(name="run_requirements_activity")
async def stub_req(input: SDLCWorkflowInput):
    return RequirementsArtifact(agent_session_id=input.run_id, version=1).model_dump()

@activity.defn(name="run_design_activity")
async def stub_design(input: SDLCWorkflowInput):
    return DesignArtifact(version=1).model_dump()

@activity.defn(name="run_development_activity")
async def stub_dev(input: SDLCWorkflowInput):
    return DevelopmentArtifact(version=1).model_dump()

@activity.defn(name="run_code_review_activity")
async def stub_code_review(input: SDLCWorkflowInput):
    return CodeReviewArtifact(
        pr_ref="https://github.com/org/repo/pull/42",
        findings=[{"id": "F-001", "severity": "medium", "description": "Missing null check"}],
        merge_recommendation="approve",
        review_summary="1 medium finding, safe to merge",
        version=1,
    ).model_dump()

@activity.defn(name="run_testing_activity")
async def stub_test(input: SDLCWorkflowInput):
    return TestingArtifact(version=1).model_dump()

@activity.defn(name="sync_run_status_activity")
async def stub_sync(*args):
    pass

@activity.defn(name="emit_escalation_activity")
async def stub_escalation(payload: dict):
    pass

ALL_STUBS = [stub_req, stub_design, stub_dev, stub_code_review, stub_test, stub_sync, stub_escalation]


@_skip
@pytest.mark.asyncio
async def test_code_review_in_pipeline():
    """Code review runs after development in a pipeline with explicit plan.

    code_review and testing share pipeline_position=4, so they are grouped
    into a single parallel phase with gate_type=all_must_approve. Both
    agents must receive an approval signal for the phase to clear.
    """
    from workflows.sdlc_workflow import SDLCWorkflow

    plan = build_execution_plan(
        run_id="r1", project_id="p1", mode="pipeline",
        active_agents=["requirements", "design", "development", "code_review", "testing"],
    )
    inp = SDLCWorkflowInput(
        run_id=str(uuid.uuid4()), project_id="p1", tenant_id="t1",
        execution_plan=plan.model_dump(),
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue="test-q",
            workflows=[SDLCWorkflow], activities=ALL_STUBS,
        ):
            handle = await env.client.start_workflow(
                SDLCWorkflow.run, inp, id=f"test-{uuid.uuid4()}", task_queue="test-q",
            )
            # Approve requirements, design, development (sequential phases).
            # code_review + testing are parallel at position 4 with
            # all_must_approve gate — both need approval signals.
            for agent_id in ("requirements", "design", "development", "code_review", "testing"):
                await handle.signal(
                    "agent_approved",
                    HITLSignal(actor_id="tester", payload={"agent_id": agent_id}),
                )

            result = await handle.result()
            assert result["status"] == "complete"
            assert "code_review" in result["phases_completed"]
            assert "testing" in result["phases_completed"]


@_skip
@pytest.mark.asyncio
async def test_code_review_standalone():
    """Code review runs standalone with a single-agent plan."""
    from workflows.sdlc_workflow import SDLCWorkflow

    plan = build_execution_plan(
        run_id="r1", project_id="p1", mode="standalone",
        active_agents=["code_review"],
    )
    inp = SDLCWorkflowInput(
        run_id=str(uuid.uuid4()), project_id="p1", tenant_id="t1",
        execution_plan=plan.model_dump(),
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue="test-q",
            workflows=[SDLCWorkflow], activities=ALL_STUBS,
        ):
            handle = await env.client.start_workflow(
                SDLCWorkflow.run, inp, id=f"test-{uuid.uuid4()}", task_queue="test-q",
            )
            await handle.signal(
                "agent_approved",
                HITLSignal(actor_id="tester", payload={"agent_id": "code_review"}),
            )

            result = await handle.result()
            assert result["status"] == "complete"
            assert result["phases_completed"] == ["code_review"]
