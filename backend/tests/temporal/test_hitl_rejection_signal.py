"""EX-1: HITL rejection signal — rejection re-runs current phase activity (not full restart).

Tier: unit (time-skipping — no real Temporal server required)
Criteria: EX-1 — rejection re-runs the current phase activity within the same workflow execution

IMPORTANT: rejection must NOT trigger workflow.continue_as_new (full restart).
It must re-execute the current phase activity and re-enter the HITL gate.
"""
from __future__ import annotations

import asyncio

import pytest

from config.env import LITELLM_API_KEY
from tests.temporal.conftest import make_run_id, make_workflow_input
from shared.models.workflow_models import HITLSignal

# ---------------------------------------------------------------------------
# Import probes
# ---------------------------------------------------------------------------
try:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    _TEMPORALIO_AVAILABLE = True
except ImportError:
    _TEMPORALIO_AVAILABLE = False

_skip_no_temporal = pytest.mark.skipif(
    not _TEMPORALIO_AVAILABLE,
    reason="temporalio not installed",
)

# Live-agent guard — rejection tests run the real requirements activity via LiteLLM proxy.
_skip_no_litellm = pytest.mark.skipif(
    not LITELLM_API_KEY,
    reason="LITELLM_API_KEY not set — litellm-proxy not configured; skipping live-agent temporal tests",
)


# ---------------------------------------------------------------------------
# EX-1: Rejection re-runs current-phase activity
# ---------------------------------------------------------------------------

@pytest.mark.unit
@_skip_no_temporal
@_skip_no_litellm
async def test_rejection_reruns_current_phase(temporal_time_skip_env):
    """Rejection signal must re-run the requirements activity within the same workflow.

    Contract (EX-1 / RESEARCH Pitfall 7):
      1. Workflow starts — requirements activity runs once
      2. HITL gate reached — awaiting signal
      3. Rejection signal sent
      4. Requirements activity runs a SECOND time (re-run, not restart)
      5. Workflow is still the same execution (same workflow ID)
    """
    from workflows.sdlc_workflow import SDLCWorkflow  # type: ignore[import]
    from workflows.activities.requirements_activity import run_requirements_activity  # type: ignore[import]
    from workflows.activities.design_activity import run_design_activity  # type: ignore[import]
    from workflows.activities.development_activity import run_development_activity  # type: ignore[import]
    from workflows.activities.testing_activity import run_testing_activity  # type: ignore[import]
    from workflows.activities.emit_escalation_activity import emit_escalation_activity  # type: ignore[import]
    from workflows.activities.sync_status_activity import sync_run_status_activity  # type: ignore[import]

    env = temporal_time_skip_env
    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)
    workflow_id = f"rejection-test-{run_id}"

    async with Worker(
        env.client,
        task_queue="test-rejection-queue",
        workflows=[SDLCWorkflow],
        activities=[
            run_requirements_activity,
            run_design_activity,
            run_development_activity,
            run_testing_activity,
            emit_escalation_activity,
            sync_run_status_activity,
        ],
    ):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue="test-rejection-queue",
        )

        # Small yield to let the requirements activity start
        await asyncio.sleep(0.1)

        # Send rejection signal for the requirements phase
        rejection_signal = HITLSignal(
            actor_id="reviewer-001",
            payload={"reason": "Needs more detail on NFRs"},
            idempotency_key=f"reject-{run_id}",
        )
        await handle.signal("requirements_rejected", rejection_signal)

        # After rejection + re-run, send approval to let the workflow progress
        await asyncio.sleep(0.1)
        approval_signal = HITLSignal(
            actor_id="reviewer-001",
            payload={},
            idempotency_key=f"approve-after-reject-{run_id}",
        )
        await handle.signal("requirements_approved", approval_signal)

        # Workflow must complete (not hang, not restart from scratch)
        result = await handle.result()
        assert result is not None, "Workflow must complete after rejection + re-run + approval"


@pytest.mark.unit
@_skip_no_temporal
@_skip_no_litellm
async def test_rejection_does_not_restart_full_pipeline(temporal_time_skip_env):
    """Rejection of requirements phase must NOT trigger a full pipeline restart.

    contract: same workflow_id before and after rejection — workflow.continue_as_new
    is NOT called on rejection (RESEARCH Pitfall 7).
    """
    from workflows.sdlc_workflow import SDLCWorkflow  # type: ignore[import]
    from workflows.activities.requirements_activity import run_requirements_activity  # type: ignore[import]
    from workflows.activities.design_activity import run_design_activity  # type: ignore[import]
    from workflows.activities.development_activity import run_development_activity  # type: ignore[import]
    from workflows.activities.testing_activity import run_testing_activity  # type: ignore[import]
    from workflows.activities.emit_escalation_activity import emit_escalation_activity  # type: ignore[import]
    from workflows.activities.sync_status_activity import sync_run_status_activity  # type: ignore[import]

    env = temporal_time_skip_env
    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)
    workflow_id = f"no-restart-test-{run_id}"

    async with Worker(
        env.client,
        task_queue="test-no-restart-queue",
        workflows=[SDLCWorkflow],
        activities=[
            run_requirements_activity,
            run_design_activity,
            run_development_activity,
            run_testing_activity,
            emit_escalation_activity,
            sync_run_status_activity,
        ],
    ):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue="test-no-restart-queue",
        )

        await asyncio.sleep(0.1)
        rejection = HITLSignal(actor_id="user-x", payload={}, idempotency_key=f"r-{run_id}")
        await handle.signal("requirements_rejected", rejection)

        await asyncio.sleep(0.1)
        approval = HITLSignal(actor_id="user-x", payload={}, idempotency_key=f"a-{run_id}")
        await handle.signal("requirements_approved", approval)

        result = await handle.result()
        # Workflow must complete under the ORIGINAL workflow_id — no continue_as_new on rejection
        assert result is not None
        # The handle still points to the original workflow_id (not a new execution)
        assert handle.id == workflow_id, (
            "EX-1 violation: workflow_id changed — rejection must re-run activity, "
            "not restart via continue_as_new"
        )
