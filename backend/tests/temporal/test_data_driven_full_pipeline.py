"""Full-pipeline integration test for the data-driven SDLCWorkflow.

Verifies that the refactored workflow:
  1. Completes all 5 phases when all gates are approved
  2. Handles rejection + re-run for a single phase
  3. Produces the same result shape as the old hardcoded workflow
"""
import asyncio
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
    RequirementsArtifact, DesignArtifact, DevelopmentArtifact, TestingArtifact,
)
from workflows.execution_plan import build_execution_plan


# ── Stub activities ──────────────────────────────────────────────────────────

@activity.defn(name="run_requirements_activity")
async def stub_req(input: SDLCWorkflowInput):
    return RequirementsArtifact(agent_session_id=input.run_id, version=1).model_dump()

@activity.defn(name="run_design_activity")
async def stub_design(input: SDLCWorkflowInput):
    return DesignArtifact(version=1).model_dump()

@activity.defn(name="run_development_activity")
async def stub_dev(input: SDLCWorkflowInput):
    return DevelopmentArtifact(version=1).model_dump()

@activity.defn(name="run_testing_activity")
async def stub_test(input: SDLCWorkflowInput):
    return TestingArtifact(version=1).model_dump()

@activity.defn(name="sync_run_status_activity")
async def stub_sync(*args):
    pass

@activity.defn(name="emit_escalation_activity")
async def stub_escalation(payload: dict):
    pass

ALL_STUBS = [stub_req, stub_design, stub_dev, stub_test, stub_sync, stub_escalation]


def _make_input(active_agents=None, skip_agents=None):
    run_id = str(uuid.uuid4())
    plan = build_execution_plan(
        run_id=run_id, project_id="proj-1", mode="pipeline",
        active_agents=active_agents, skip_agents=skip_agents,
    )
    return SDLCWorkflowInput(
        run_id=run_id, project_id="proj-1", tenant_id="t1",
        execution_plan=plan.model_dump(),
    )


@_skip
@pytest.mark.asyncio
async def test_full_pipeline_all_approved():
    """All 4 runnable phases complete: requirements, design, development, testing —
    all gated (approval_required). Deployment is included in active_agents but has no
    registered activity so the workflow skips it."""
    from workflows.sdlc_workflow import SDLCWorkflow

    inp = _make_input(active_agents=["requirements", "design", "development", "testing", "deployment"])

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue="full-q",
            workflows=[SDLCWorkflow], activities=ALL_STUBS,
        ):
            handle = await env.client.start_workflow(
                SDLCWorkflow.run, inp, id=f"full-{uuid.uuid4()}", task_queue="full-q",
            )
            # Approve every gated phase (testing is now approval_required too).
            for agent_id in ("requirements", "design", "development", "testing"):
                await handle.signal(
                    "agent_approved",
                    HITLSignal(actor_id="tester", payload={"agent_id": agent_id}),
                )

            result = await handle.result()
            assert result["status"] == "complete"
            assert set(result["phases_completed"]) == {
                "requirements", "design", "development", "testing",
            }
            # deployment has no activity -> skipped, not in phases_completed
            assert "deployment" not in result["phases_completed"]
            # Result shape: must have run_id and phases_completed (backward compat)
            assert "run_id" in result
            assert isinstance(result["phases_completed"], list)


@_skip
@pytest.mark.asyncio
async def test_rejection_reruns_phase():
    """Rejecting requirements causes a re-run; subsequent approval completes the pipeline."""
    from workflows.sdlc_workflow import SDLCWorkflow

    inp = _make_input(active_agents=["requirements", "design"])

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue="rej-q",
            workflows=[SDLCWorkflow], activities=ALL_STUBS,
        ):
            handle = await env.client.start_workflow(
                SDLCWorkflow.run, inp, id=f"rej-{uuid.uuid4()}", task_queue="rej-q",
            )
            # Reject requirements first — the workflow re-runs the activity
            await handle.signal(
                "agent_rejected",
                HITLSignal(actor_id="tester", payload={"agent_id": "requirements"}),
            )
            # Small delay so the worker processes the rejection before approval
            await asyncio.sleep(0.1)
            # Then approve on the re-run
            await handle.signal(
                "agent_approved",
                HITLSignal(actor_id="tester", payload={"agent_id": "requirements"}),
            )
            # Approve design
            await handle.signal(
                "agent_approved",
                HITLSignal(actor_id="tester", payload={"agent_id": "design"}),
            )

            result = await handle.result()
            assert result["status"] == "complete"
            assert "requirements" in result["phases_completed"]
            assert "design" in result["phases_completed"]


@_skip
@pytest.mark.asyncio
async def test_backward_compat_signals():
    """Old-style per-phase signals (requirements_approved) still drive the workflow."""
    from workflows.sdlc_workflow import SDLCWorkflow

    inp = _make_input(active_agents=["requirements"])

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue="compat-q",
            workflows=[SDLCWorkflow], activities=ALL_STUBS,
        ):
            handle = await env.client.start_workflow(
                SDLCWorkflow.run, inp, id=f"compat-{uuid.uuid4()}", task_queue="compat-q",
            )
            # Use the OLD signal name
            await handle.signal(
                "requirements_approved",
                HITLSignal(actor_id="tester"),
            )

            result = await handle.result()
            assert result["status"] == "complete"
            assert result["phases_completed"] == ["requirements"]
