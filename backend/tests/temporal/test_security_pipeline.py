"""Integration tests: Security Agent in the data-driven workflow.

Proves the security agent works in:
  1. Pipeline mode (parallel with code_review after development)
  2. Standalone mode (single-agent scan)
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
    TestingArtifact, CodeReviewArtifact, SecurityArtifact,
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
async def stub_cr(input: SDLCWorkflowInput):
    return CodeReviewArtifact(pr_ref="PR#1", merge_recommendation="approve", version=1).model_dump()

@activity.defn(name="run_security_activity")
async def stub_sec(input: SDLCWorkflowInput):
    return SecurityArtifact(
        scope="full",
        risk_score="low",
        security_sign_off=True,
        scan_summary="No critical findings",
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

ALL_STUBS = [stub_req, stub_design, stub_dev, stub_cr, stub_sec, stub_test, stub_sync, stub_escalation]


@_skip
@pytest.mark.asyncio
async def test_security_parallel_with_code_review():
    """Security and code_review run in a parallel phase (both pipeline_position=4)."""
    from workflows.sdlc_workflow import SDLCWorkflow

    plan = build_execution_plan(
        run_id="r1", project_id="p1", mode="pipeline",
        active_agents=["requirements", "design", "development", "code_review", "security", "testing"],
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
            for agent_id in ("requirements", "design", "development", "code_review", "security", "testing"):
                await handle.signal(
                    "agent_approved",
                    HITLSignal(actor_id="tester", payload={"agent_id": agent_id}),
                )

            result = await handle.result()
            assert result["status"] == "complete"
            assert "security" in result["phases_completed"]
            assert "code_review" in result["phases_completed"]


@_skip
@pytest.mark.asyncio
async def test_security_standalone():
    """Security runs standalone with a single-agent plan."""
    from workflows.sdlc_workflow import SDLCWorkflow

    plan = build_execution_plan(
        run_id="r1", project_id="p1", mode="standalone",
        active_agents=["security"],
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
                HITLSignal(actor_id="tester", payload={"agent_id": "security"}),
            )

            result = await handle.result()
            assert result["status"] == "complete"
            assert result["phases_completed"] == ["security"]
