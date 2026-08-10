"""REQ-M10-04: within-agent clarification SLA timer fires escalation, gate stays open.

Tier: unit (time-skipping — no real Temporal server, no LLM, no Postgres).
Criteria: when no within_agent_clarification signal is sent, virtual time
advances past SLA_CLARIFICATION_HOURS + SLA_GRACE_MINUTES and
emit_escalation_activity fires (T-10.2-02/03) WITHOUT closing the gate — a
late signal sent after escalation still releases it (D-M10-04).

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

    _CLARIFICATION_ID = str(uuid.uuid4())

    # Module-level counter — incremented by mock_emit_escalation_activity so
    # the test can assert it ran at least once.
    _escalation_calls = {"count": 0}

    @activity.defn(name="run_requirements_activity")
    async def mock_requirements_activity_always_clarifies(input):
        """Returns a ClarificationRequest until clarification_answer is set.

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
        _escalation_calls["count"] += 1
        return None

    @activity.defn(name="sync_run_status_activity")
    async def mock_sync_run_status_activity(*args, **kwargs):
        return None


@pytest.mark.unit
@_skip_no_temporal
async def test_clarification_sla_escalates_and_keeps_gate_open(temporal_time_skip_env):
    """SLA breach (no signal sent) fires emit_escalation_activity; a late
    signal sent afterwards still releases the gate (gate stays open)."""
    from workflows.sdlc_workflow import SDLCWorkflow

    env = temporal_time_skip_env
    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)
    _escalation_calls["count"] = 0

    async with Worker(
        env.client,
        task_queue="test-clarification-sla-queue",
        workflows=[SDLCWorkflow],
        activities=[
            mock_requirements_activity_always_clarifies,
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
            id=f"clarification-sla-test-{run_id}",
            task_queue="test-clarification-sla-queue",
        )

        # Do not send a within_agent_clarification signal — let the SLA fire.
        # Time-skipping advances virtual time instantly past
        # SLA_CLARIFICATION_HOURS + SLA_GRACE_MINUTES.
        # Poll describe() under time-skipping until at least one escalation
        # has been recorded (the workflow remains suspended in the gate
        # re-entering wait_condition after the first breach).
        async def _wait_for_escalation():
            # SLA_CLARIFICATION_HOURS (default 4) + SLA_GRACE_MINUTES (default 5)
            # = 245 minutes; advance virtual time in 10-minute steps with margin.
            for _ in range(40):
                if _escalation_calls["count"] >= 1:
                    return
                await env.sleep(600)  # advance virtual time by 10 minutes
            raise AssertionError("emit_escalation_activity was not invoked")

        await _wait_for_escalation()
        assert _escalation_calls["count"] >= 1

        desc = await handle.describe()
        assert desc.status.name == "RUNNING", "gate must stay open after SLA breach"

        # Late answer after escalation still releases the gate.
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
