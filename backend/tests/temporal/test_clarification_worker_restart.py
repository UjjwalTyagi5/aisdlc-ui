"""REQ-M10-08 (code-now half): live worker-restart-during-clarification proof.

DLT-10: requires a live Temporal worker process + Postgres — deferred to
settingup. This module documents the intended live flow as a fully authored
(non-`pass`) test body, decorated `@pytest.mark.skip` so it collects and is
visible in the suite without executing.

Why this matters: _run_phase_with_clarification suspends the workflow on
`workflow.wait_condition` while awaiting a `within_agent_clarification`
signal. Temporal persists this suspension in the workflow's event history, so
killing and restarting the worker process must NOT lose the pending
clarification — on restart, the worker replays history, re-enters the same
`wait_condition`, and a subsequently delivered signal resumes the run with
identical pre-question context (clarification_id, questions, thread_id).
This is THE durability guarantee of moving HITL suspension out of in-process
LangGraph `interrupt()` and into Temporal signals (REQ-M10-07/08).

The time-skipping environment used by the rest of this suite cannot exercise
this: `WorkflowEnvironment.start_time_skipping()` runs an in-memory test
server with no separate worker process to kill. A real proof requires
`WorkflowEnvironment.start_local()` (or a real Temporal server) plus an
actual `python -m workflows.worker`-style process that can be terminated and
restarted out-of-band — that live infrastructure is DLT-10.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.temporal.conftest import make_run_id, make_workflow_input

try:
    from temporalio import activity
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    _TEMPORALIO_AVAILABLE = True
except ImportError:
    _TEMPORALIO_AVAILABLE = False

_skip_no_temporal = pytest.mark.skipif(
    not _TEMPORALIO_AVAILABLE,
    reason="temporalio not installed",
)


if _TEMPORALIO_AVAILABLE:
    from shared.models.workflow_models import ClarificationAnswer, ClarificationRequest, HITLSignal

    _RESTART_CLARIFICATION_ID = str(uuid.uuid4())

    @activity.defn(name="run_requirements_activity")
    async def mock_requirements_activity_restart(input):
        clarification_answer = input.get("clarification_answer")
        if clarification_answer:
            return {
                "agent_session_id": input["run_id"],
                "brd_content": f"BRD finalized after clarification: {clarification_answer}",
                "version": input.get("agent_version", 1),
            }
        return ClarificationRequest(
            questions=["What is the scope?"],
            thread_id=input["run_id"],
            agent_type="requirements",
            phase="requirements",
            clarification_id=_RESTART_CLARIFICATION_ID,
        ).model_dump()

    @activity.defn(name="run_design_activity")
    async def mock_design_activity_restart(input):
        return {"version": input.get("agent_version", 1)}

    @activity.defn(name="run_development_activity")
    async def mock_development_activity_restart(input):
        return {"version": input.get("agent_version", 1)}

    @activity.defn(name="run_testing_activity")
    async def mock_testing_activity_restart(input):
        return {"version": input.get("agent_version", 1)}

    @activity.defn(name="run_code_review_activity")
    async def mock_code_review_activity_restart(input):
        return {"review_summary": "Code review OK.", "version": input.get("agent_version", 1)}

    @activity.defn(name="emit_escalation_activity")
    async def mock_emit_escalation_activity_restart(payload):
        return None

    @activity.defn(name="sync_run_status_activity")
    async def mock_sync_run_status_activity_restart(*args, **kwargs):
        return None

    _ALL_MOCK_ACTIVITIES_RESTART = [
        mock_requirements_activity_restart,
        mock_design_activity_restart,
        mock_development_activity_restart,
        mock_testing_activity_restart,
        mock_code_review_activity_restart,
        mock_emit_escalation_activity_restart,
        mock_sync_run_status_activity_restart,
    ]


def _approval_signal():
    from shared.models.workflow_models import HITLSignal

    return HITLSignal(actor_id="test-user", payload={}, idempotency_key=str(uuid.uuid4()))


@pytest.mark.unit
@_skip_no_temporal
@pytest.mark.skip(
    reason="DLT-10: requires live Temporal worker + Postgres — deferred to settingup"
)
async def test_clarification_worker_restart(temporal_env):
    """Live proof: a worker killed while a run is suspended on
    within_agent_clarification resumes correctly from Temporal history after
    restart, with identical pre-question context.

    DLT-10 — authored now, executed later against a real Temporal server +
    an out-of-band-restartable worker process.
    """
    from workflows.sdlc_workflow import SDLCWorkflow

    env = temporal_env
    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)
    task_queue = f"test-clarification-restart-queue-{run_id}"

    # 1. Start a worker and the workflow; drive the run to the requirements
    #    clarification gate (the mock activity returns a ClarificationRequest
    #    on its first invocation).
    worker = Worker(
        env.client,
        task_queue=task_queue,
        workflows=[SDLCWorkflow],
        activities=_ALL_MOCK_ACTIVITIES_RESTART,
    )
    worker_task = asyncio.create_task(worker.run())
    try:
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            workflow_input,
            id=f"clarification-restart-test-{run_id}",
            task_queue=task_queue,
        )

        # Give the worker time to process the workflow task and reach the
        # suspended wait_condition on the requirements ClarificationRequest.
        await asyncio.sleep(1)
        desc = await handle.describe()
        assert desc.status.name == "RUNNING"

        # 2. Kill the worker mid-suspension — simulates a process crash /
        #    deploy restart while a run is awaiting clarification. The
        #    pending clarification (clarification_id, questions, thread_id)
        #    is durable in Temporal's workflow event history, NOT in worker
        #    process memory.
        await worker.shutdown()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

        # 3. Restart a fresh worker against the SAME task queue. On replay,
        #    the workflow re-enters the same wait_condition for
        #    within_agent_clarification — the pending clarification context
        #    (clarification_id == _RESTART_CLARIFICATION_ID) must be
        #    unchanged from before the crash.
        restarted_worker = Worker(
            env.client,
            task_queue=task_queue,
            workflows=[SDLCWorkflow],
            activities=_ALL_MOCK_ACTIVITIES_RESTART,
        )
        restarted_task = asyncio.create_task(restarted_worker.run())
        try:
            # 4. Send the matching clarification answer — only a signal whose
            #    clarification_id equals the PRE-CRASH _RESTART_CLARIFICATION_ID
            #    releases the gate, proving the suspended state survived the
            #    restart with identical pre-question context.
            await handle.signal(
                SDLCWorkflow.within_agent_clarification,
                ClarificationAnswer(
                    clarification_id=_RESTART_CLARIFICATION_ID,
                    answer="The scope is the checkout flow only.",
                    actor_id="test-user",
                ),
            )

            # Approve every downstream gate so the workflow runs to
            # completion.
            await handle.signal(SDLCWorkflow.requirements_approved, _approval_signal())
            await handle.signal(SDLCWorkflow.design_approved, _approval_signal())
            await handle.signal(SDLCWorkflow.development_approved, _approval_signal())

            result = await handle.result()
            assert result["status"] == "complete"
        finally:
            await restarted_worker.shutdown()
            restarted_task.cancel()
            try:
                await restarted_task
            except asyncio.CancelledError:
                pass
    finally:
        if not worker_task.done():
            await worker.shutdown()
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
