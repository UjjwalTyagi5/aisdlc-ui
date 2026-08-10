"""REQ-M10-08: end-to-end within-agent clarification — question -> Temporal
signal -> agent resumes.

Tier: unit (time-skipping — no real Temporal server, no LLM, no Postgres).

This is the closing E2E proof for milestone-10: a phase activity
(requirements) returns a ClarificationRequest, the test sends a
within_agent_clarification signal carrying a matching ClarificationAnswer,
the workflow re-invokes the activity (with clarification_answer injected) via
_run_phase_with_clarification, and the run advances past the clarification
gate to completion.

All activities are MOCKED (registered under the real activity names) so the
test is fully autonomous — DLT-10 owns live worker-restart durability
(see test_clarification_worker_restart.py).
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

    # Fixed clarification_id so the test can construct a matching
    # ClarificationAnswer without querying workflow state.
    _CLARIFICATION_ID = str(uuid.uuid4())

    # Module-level call counter — distinguishes call 1 (ClarificationRequest)
    # from call 2 (resumed RequirementsArtifact).
    _requirements_calls = {"n": 0}

    def _reset_call_counter() -> None:
        _requirements_calls["n"] = 0

    @activity.defn(name="run_requirements_activity")
    async def mock_requirements_activity(input):
        """First call: ClarificationRequest. Second call (clarification_answer
        set): RequirementsArtifact dict — proving the resume actually
        happened (T-10.3-07)."""
        _requirements_calls["n"] += 1
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
            clarification_id=_CLARIFICATION_ID,
        ).model_dump()

    @activity.defn(name="run_design_activity")
    async def mock_design_activity(input):
        return {"version": input.get("agent_version", 1)}

    @activity.defn(name="run_development_activity")
    async def mock_development_activity(input):
        return {"version": input.get("agent_version", 1)}

    @activity.defn(name="run_testing_activity")
    async def mock_testing_activity(input):
        return {"version": input.get("agent_version", 1)}

    @activity.defn(name="run_code_review_activity")
    async def mock_code_review_activity(input):
        return {"review_summary": "Code review OK.", "version": input.get("agent_version", 1)}

    @activity.defn(name="run_security_activity")
    async def mock_security_activity(input):
        return {"scan_summary": "No findings.", "version": input.get("agent_version", 1)}

    @activity.defn(name="emit_escalation_activity")
    async def mock_emit_escalation_activity(payload):
        return None

    @activity.defn(name="sync_run_status_activity")
    async def mock_sync_run_status_activity(*args, **kwargs):
        return None

    _ALL_MOCK_ACTIVITIES = [
        mock_requirements_activity,
        mock_design_activity,
        mock_development_activity,
        mock_testing_activity,
        mock_code_review_activity,
        mock_security_activity,
        mock_emit_escalation_activity,
        mock_sync_run_status_activity,
    ]


def _approval_signal():
    from shared.models.workflow_models import HITLSignal

    return HITLSignal(actor_id="test-user", payload={}, idempotency_key=str(uuid.uuid4()))


async def _wait_until(predicate, timeout: float = 10.0, interval: float = 0.05) -> None:
    """Poll `predicate()` until truthy or `timeout` (wall-clock) seconds elapse.

    Mirrors test_clarification_all_phases.py's helper: the time-skipping
    environment auto-advances workflow timers, but workflow-task processing
    still happens on real asyncio ticks. Polling lets the workflow task run
    to the point where the requirements activity has been invoked (and
    suspended on the ClarificationRequest) before the test sends the
    within_agent_clarification signal — sending it too early would arrive
    before any clarification is pending and be silently dropped by the
    correlation guard.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


@pytest.mark.unit
@_skip_no_temporal
async def test_within_agent_clarification_via_temporal(temporal_time_skip_env):
    """E2E: question (ClarificationRequest) -> within_agent_clarification
    signal (matching clarification_id) -> agent resumes via
    _run_phase_with_clarification -> run advances to completion.

    REQ-M10-08.
    """
    from workflows.sdlc_workflow import SDLCWorkflow

    _reset_call_counter()
    env = temporal_time_skip_env
    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)

    async with Worker(
        env.client,
        task_queue="test-e2e-clarification-queue",
        workflows=[SDLCWorkflow],
        activities=_ALL_MOCK_ACTIVITIES,
    ):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            workflow_input,
            id=f"e2e-clarification-test-{run_id}",
            task_queue="test-e2e-clarification-queue",
        )

        # Wait for the workflow task to invoke the requirements activity
        # (call 1 returns the ClarificationRequest and the workflow suspends
        # on within_agent_clarification).
        await _wait_until(lambda: _requirements_calls["n"] >= 1)

        # Send the clarification answer with the matching clarification_id —
        # releases the requirements gate and triggers the resumed re-invoke.
        await handle.signal(
            SDLCWorkflow.within_agent_clarification,
            ClarificationAnswer(
                clarification_id=_CLARIFICATION_ID,
                answer="The scope is the checkout flow only.",
                actor_id="test-user",
            ),
        )

        # Approve every downstream gate so the workflow runs to completion
        # without waiting on the 24h+ SLA timers.
        await handle.signal(SDLCWorkflow.requirements_approved, _approval_signal())
        await handle.signal(SDLCWorkflow.design_approved, _approval_signal())
        await handle.signal(SDLCWorkflow.development_approved, _approval_signal())

        result = await handle.result()

        assert result["status"] == "complete"
        # T-10.3-07: a no-op that never resumed would leave the call count at
        # 1 — assert the activity was actually re-invoked with the answer.
        assert _requirements_calls["n"] == 2, (
            "requirements activity must be re-invoked after the matching "
            "within_agent_clarification signal (question -> signal -> resume)"
        )
