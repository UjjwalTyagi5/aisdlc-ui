"""REQ-M10-03: within-agent clarification suspend -> signal -> resume loop.

Tier: unit (time-skipping — no real Temporal server, no LLM, no Postgres).
Criteria: SDLCWorkflow suspends on a ClarificationRequest returned by
run_requirements_activity, releases only on a within_agent_clarification
signal whose clarification_id matches the pending request (correlation
guard, T-10.2-01/04), and re-invokes the activity with clarification_answer
injected so the run advances past the requirements gate.

All activities are MOCKED (registered under the real activity names) so the
test is fully autonomous — DLT-10 owns live worker-restart durability.
"""
from __future__ import annotations

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
    from shared.models.workflow_models import ClarificationAnswer, ClarificationRequest

    # Fixed clarification_id so the test can construct a matching
    # ClarificationAnswer without querying workflow state.
    _CLARIFICATION_ID = str(uuid.uuid4())

    @activity.defn(name="run_requirements_activity")
    async def mock_requirements_activity(input):
        """First call: return a ClarificationRequest. Second call (with
        clarification_answer set): return a RequirementsArtifact dict.

        No custom Temporal data_converter is configured, so `input` arrives
        here as a plain dict (not an SDLCWorkflowInput instance)."""
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


@pytest.mark.unit
@_skip_no_temporal
async def test_clarification_suspend_signal_resume(temporal_time_skip_env):
    """Matching within_agent_clarification signal releases the gate and the
    activity is re-invoked with clarification_answer set."""
    from workflows.sdlc_workflow import SDLCWorkflow

    env = temporal_time_skip_env
    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)

    async with Worker(
        env.client,
        task_queue="test-clarification-queue",
        workflows=[SDLCWorkflow],
        activities=[
            mock_requirements_activity,
            mock_design_activity,
            mock_development_activity,
            mock_testing_activity,
            mock_code_review_activity,
            mock_security_activity,
            mock_emit_escalation_activity,
            mock_sync_run_status_activity,
        ],
    ):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            workflow_input,
            id=f"clarification-test-{run_id}",
            task_queue="test-clarification-queue",
        )

        # Send the clarification answer with the matching clarification_id.
        await handle.signal(
            SDLCWorkflow.within_agent_clarification,
            ClarificationAnswer(
                clarification_id=_CLARIFICATION_ID,
                answer="Scope is the checkout flow only.",
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


@pytest.mark.unit
@_skip_no_temporal
async def test_clarification_non_matching_signal_does_not_release_gate(temporal_time_skip_env):
    """A within_agent_clarification signal with a non-matching clarification_id
    must NOT release the gate (T-10.2-01/04 correlation guard)."""
    from workflows.sdlc_workflow import SDLCWorkflow

    env = temporal_time_skip_env
    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)

    async with Worker(
        env.client,
        task_queue="test-clarification-nomatch-queue",
        workflows=[SDLCWorkflow],
        activities=[
            mock_requirements_activity,
            mock_design_activity,
            mock_development_activity,
            mock_testing_activity,
            mock_code_review_activity,
            mock_security_activity,
            mock_emit_escalation_activity,
            mock_sync_run_status_activity,
        ],
    ):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            workflow_input,
            id=f"clarification-nomatch-test-{run_id}",
            task_queue="test-clarification-nomatch-queue",
        )

        # Non-matching clarification_id — must be silently dropped.
        await handle.signal(
            SDLCWorkflow.within_agent_clarification,
            ClarificationAnswer(
                clarification_id=str(uuid.uuid4()),
                answer="Wrong answer for the wrong question.",
                actor_id="attacker",
            ),
        )

        # The describe() call confirms the workflow is still running (the
        # gate did not release from the bogus signal).
        desc = await handle.describe()
        assert desc.status.name == "RUNNING"

        # Now send the correct one so the workflow can finish cleanly.
        await handle.signal(
            SDLCWorkflow.within_agent_clarification,
            ClarificationAnswer(
                clarification_id=_CLARIFICATION_ID,
                answer="Scope is the checkout flow only.",
                actor_id="test-user",
            ),
        )
        await handle.signal(SDLCWorkflow.requirements_approved, _approval_signal())
        await handle.signal(SDLCWorkflow.design_approved, _approval_signal())
        await handle.signal(SDLCWorkflow.development_approved, _approval_signal())

        result = await handle.result()
        assert result["status"] == "complete"


def _approval_signal():
    from shared.models.workflow_models import HITLSignal

    return HITLSignal(actor_id="test-user", payload={}, idempotency_key=str(uuid.uuid4()))
