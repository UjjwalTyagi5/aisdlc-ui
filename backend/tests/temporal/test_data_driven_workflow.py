"""Test data-driven SDLCWorkflow with execution plan.

Uses stub activities that return immediately (no LLM calls).
Uses time-skipping env for SLA tests.
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
from shared.models.artifacts import RequirementsArtifact, DesignArtifact
from workflows.execution_plan import build_execution_plan


# ── Stub activities ──────────────────────────────────────────────────────────

# Per-run invocation counter so the rejection test can assert the activity
# actually re-ran (a vacuous reject+approve would never re-run).
REQ_CALLS: dict[str, int] = {}


@activity.defn(name="run_requirements_activity")
async def stub_requirements(input: SDLCWorkflowInput) -> dict:
    REQ_CALLS[input.run_id] = REQ_CALLS.get(input.run_id, 0) + 1
    return RequirementsArtifact(agent_session_id=input.run_id, version=1).model_dump()


@activity.defn(name="run_design_activity")
async def stub_design(input: SDLCWorkflowInput) -> dict:
    return DesignArtifact(version=1).model_dump()


@activity.defn(name="run_development_activity")
async def stub_development(input: SDLCWorkflowInput) -> dict:
    return {"version": 1}


@activity.defn(name="run_testing_activity")
async def stub_testing(input: SDLCWorkflowInput) -> dict:
    return {"version": 1}


@activity.defn(name="run_code_review_activity")
async def stub_code_review(input: SDLCWorkflowInput) -> dict:
    return {"review_summary": "Code review OK.", "version": 1}


@activity.defn(name="run_security_activity")
async def stub_security(input: SDLCWorkflowInput) -> dict:
    from shared.models.artifacts import SecurityArtifact
    return SecurityArtifact(version=1).model_dump()


@activity.defn(name="sync_run_status_activity")
async def stub_sync(*args) -> None:
    pass


@activity.defn(name="emit_escalation_activity")
async def stub_escalation(payload: dict) -> None:
    pass


STUB_ACTIVITIES = [stub_requirements, stub_design, stub_sync, stub_escalation]

# Full set including development + testing — used by the no-plan default-pipeline test,
# which exercises every agent that has a registered activity (deployment has none).
FULL_STUB_ACTIVITIES = STUB_ACTIVITIES + [stub_development, stub_testing, stub_code_review, stub_security]


@_skip
@pytest.mark.asyncio
async def test_two_phase_pipeline_completes():
    """A 2-phase plan (requirements -> design) completes with approval signals."""
    from workflows.sdlc_workflow import SDLCWorkflow

    plan = build_execution_plan(
        run_id="r1", project_id="p1", mode="pipeline",
        active_agents=["requirements", "design"],
    )
    inp = SDLCWorkflowInput(
        run_id="r1", project_id="p1", tenant_id="t1",
        execution_plan=plan.model_dump(),
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue="test-q",
            workflows=[SDLCWorkflow], activities=STUB_ACTIVITIES,
        ):
            handle = await env.client.start_workflow(
                SDLCWorkflow.run, inp, id=f"test-{uuid.uuid4()}", task_queue="test-q",
            )
            # Send approval signals for both phases
            await handle.signal("agent_approved", HITLSignal(actor_id="tester", payload={"agent_id": "requirements"}))
            await handle.signal("agent_approved", HITLSignal(actor_id="tester", payload={"agent_id": "design"}))

            result = await handle.result()
            assert result["status"] == "complete"
            assert "requirements" in result["phases_completed"]
            assert "design" in result["phases_completed"]


@_skip
@pytest.mark.asyncio
async def test_backward_compat_signal_aliases():
    """Old per-phase signals (requirements_approved/design_approved) still drive the loop."""
    from workflows.sdlc_workflow import SDLCWorkflow

    plan = build_execution_plan(
        run_id="r2", project_id="p1", mode="pipeline",
        active_agents=["requirements", "design"],
    )
    inp = SDLCWorkflowInput(
        run_id="r2", project_id="p1", tenant_id="t1",
        execution_plan=plan.model_dump(),
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue="test-q2",
            workflows=[SDLCWorkflow], activities=STUB_ACTIVITIES,
        ):
            handle = await env.client.start_workflow(
                SDLCWorkflow.run, inp, id=f"test-{uuid.uuid4()}", task_queue="test-q2",
            )
            await handle.signal("requirements_approved", HITLSignal(actor_id="tester", payload={}))
            await handle.signal("design_approved", HITLSignal(actor_id="tester", payload={}))

            result = await handle.result()
            assert result["status"] == "complete"
            assert "design" in result["phases_completed"]


@_skip
@pytest.mark.asyncio
async def test_rejection_reruns_and_regates():
    """A rejection re-runs the phase activity and re-enters the gate; approval then completes."""
    from workflows.sdlc_workflow import SDLCWorkflow

    plan = build_execution_plan(
        run_id="r3", project_id="p1", mode="pipeline",
        active_agents=["requirements"],
    )
    inp = SDLCWorkflowInput(
        run_id="r3", project_id="p1", tenant_id="t1",
        execution_plan=plan.model_dump(),
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue="test-q3",
            workflows=[SDLCWorkflow], activities=STUB_ACTIVITIES,
        ):
            handle = await env.client.start_workflow(
                SDLCWorkflow.run, inp, id=f"test-{uuid.uuid4()}", task_queue="test-q3",
            )
            # Reject first (let the worker process it and re-run the activity),
            # then approve — exercises the re-run + re-gate loop (max_rejections=1).
            # The ordering matters: a simultaneous reject+approve would set both
            # flags and _is_phase_rejected would be False (approval masks
            # rejection), so the re-run path would never execute.
            await handle.signal("agent_rejected", HITLSignal(actor_id="tester", payload={"agent_id": "requirements"}))
            await asyncio.sleep(0.1)
            await handle.signal("agent_approved", HITLSignal(actor_id="tester", payload={"agent_id": "requirements"}))

            result = await handle.result()
            assert result["status"] == "complete"
            assert "requirements" in result["phases_completed"]
            # The activity ran twice: initial run + one re-run after rejection.
            assert REQ_CALLS["r3"] == 2


@_skip
@pytest.mark.asyncio
async def test_no_plan_defaults_and_skips_missing_activities():
    """With no execution_plan the workflow builds a default plan from the registry and
    skips agents that have no registered activity (development/testing/deployment here)."""
    from workflows.sdlc_workflow import SDLCWorkflow

    inp = SDLCWorkflowInput(run_id="r4", project_id="p1", tenant_id="t1")

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue="test-q4",
            workflows=[SDLCWorkflow], activities=FULL_STUB_ACTIVITIES,
        ):
            handle = await env.client.start_workflow(
                SDLCWorkflow.run, inp, id=f"test-{uuid.uuid4()}", task_queue="test-q4",
            )
            # Default pipeline gates requirements, design, development;
            # testing + code_review + security share pipeline_position=4 (parallel, all_must_approve);
            # deployment has no activity -> skipped.
            await handle.signal("requirements_approved", HITLSignal(actor_id="tester", payload={}))
            await handle.signal("design_approved", HITLSignal(actor_id="tester", payload={}))
            await handle.signal("development_approved", HITLSignal(actor_id="tester", payload={}))
            # Phase 4 is parallel (testing + code_review + security) with all_must_approve gate
            await handle.signal("agent_approved", HITLSignal(actor_id="tester", payload={"agent_id": "testing"}))
            await handle.signal("agent_approved", HITLSignal(actor_id="tester", payload={"agent_id": "code_review"}))
            await handle.signal("agent_approved", HITLSignal(actor_id="tester", payload={"agent_id": "security"}))

            result = await handle.result()
            assert result["status"] == "complete"
            assert set(result["phases_completed"]) == {"requirements", "design", "development", "testing", "code_review", "security"}
            # deployment is in the registry but has no Temporal activity -> skipped
            assert "deployment" not in result["phases_completed"]
