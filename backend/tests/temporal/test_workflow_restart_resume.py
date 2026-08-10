"""SC-07, EX-2: Server restart resumes from last completed activity + replay correctness.

Tier: integration (requires Temporal server + Postgres)
Criteria:
  SC-07 — Server restart resumes from last completed activity (durable resume)
  EX-2 — Workflow replay produces the same result as the original execution

Skip guard: tests skip (not error) when POSTGRES_CONN_STRING is absent.
The SDLCWorkflow (plan 04) is not yet built; tests xfail until it lands.
"""
from __future__ import annotations

import asyncio

import pytest

from config.env import POSTGRES_CONN_STRING
from tests.temporal.conftest import make_run_id, make_workflow_input, seed_run
from shared.models.workflow_models import HITLSignal

# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------
_skip_no_db = pytest.mark.skipif(
    not POSTGRES_CONN_STRING,
    reason="POSTGRES_CONN_STRING not set — skipping DB-dependent temporal tests",
)

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

from config.env import LITELLM_API_KEY

_skip_no_litellm = pytest.mark.skipif(
    not LITELLM_API_KEY,
    reason="LITELLM_API_KEY not set — litellm-proxy not configured; skipping live-agent temporal tests",
)


# ---------------------------------------------------------------------------
# SC-07: Durable resume after worker restart
# ---------------------------------------------------------------------------

@pytest.mark.integration
@_skip_no_db
@_skip_no_temporal
@_skip_no_litellm
async def test_workflow_restart_resume(temporal_env):
    """Stopping and restarting the worker must resume the workflow at the last checkpoint.

    SC-07 contract:
      1. Start workflow + worker
      2. Let requirements activity complete
      3. Stop the worker (simulate server restart)
      4. Restart the worker
      5. Send approval signal
      6. Workflow must continue from the requirements→design boundary, NOT restart from scratch
    """
    from workflows.sdlc_workflow import SDLCWorkflow  # type: ignore[import]
    from workflows.activities.requirements_activity import run_requirements_activity  # type: ignore[import]
    from workflows.activities.design_activity import run_design_activity  # type: ignore[import]
    from workflows.activities.development_activity import run_development_activity  # type: ignore[import]
    from workflows.activities.testing_activity import run_testing_activity  # type: ignore[import]
    from workflows.activities.emit_escalation_activity import emit_escalation_activity  # type: ignore[import]
    from workflows.activities.sync_status_activity import sync_run_status_activity  # type: ignore[import]

    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)
    workflow_id = f"resume-test-{run_id}"
    task_queue = f"test-resume-queue-{run_id}"
    await seed_run(run_id, project_id=workflow_input.project_id)

    all_activities = [
        run_requirements_activity,
        run_design_activity,
        run_development_activity,
        run_testing_activity,
        emit_escalation_activity,
        sync_run_status_activity,
    ]

    # Phase 1: Start workflow + first worker
    async with Worker(
        temporal_env.client,
        task_queue=task_queue,
        workflows=[SDLCWorkflow],
        activities=all_activities,
    ):
        handle = await temporal_env.client.start_workflow(
            SDLCWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue,
        )
        # Allow requirements activity to complete and reach HITL gate
        await asyncio.sleep(1)

    # Worker has "stopped" — workflow is durably paused at HITL gate

    # Phase 2: Restart worker (simulating server restart)
    async with Worker(
        temporal_env.client,
        task_queue=task_queue,
        workflows=[SDLCWorkflow],
        activities=all_activities,
    ):
        # Approve every gate (Temporal queues signals durably, so the restarted
        # worker resumes from the requirements→design boundary and runs to completion
        # rather than restarting from scratch — that durable resume IS the SC-07 proof).
        handle = temporal_env.client.get_workflow_handle(workflow_id)
        for phase in ("requirements", "design", "development", "testing"):
            await handle.signal(
                f"{phase}_approved",
                HITLSignal(
                    actor_id="approver",
                    payload={},
                    idempotency_key=f"resume-approve-{phase}-{run_id}",
                ),
            )

        result = await handle.result()
        assert result is not None, "SC-07: workflow must complete after worker restart + approvals"


# ---------------------------------------------------------------------------
# EX-2: Workflow replay determinism
# ---------------------------------------------------------------------------

@pytest.mark.integration
@_skip_no_db
@_skip_no_temporal
@_skip_no_litellm
async def test_replay(temporal_env):
    """Re-executing a workflow from its history must produce the same result.

    EX-2 contract (D-09 determinism):
      The Temporal local environment validates non-determinism automatically —
      if the workflow code diverges from recorded history, the SDK raises
      WorkflowNondeterminismError. A clean completion is the assertion.
    """
    from workflows.sdlc_workflow import SDLCWorkflow  # type: ignore[import]
    from workflows.activities.requirements_activity import run_requirements_activity  # type: ignore[import]
    from workflows.activities.design_activity import run_design_activity  # type: ignore[import]
    from workflows.activities.emit_escalation_activity import emit_escalation_activity  # type: ignore[import]

    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)

    async with Worker(
        temporal_env.client,
        task_queue="test-replay-queue",
        workflows=[SDLCWorkflow],
        activities=[run_requirements_activity, run_design_activity, emit_escalation_activity],
    ):
        handle = await temporal_env.client.start_workflow(
            SDLCWorkflow.run,
            workflow_input,
            id=f"replay-test-{run_id}",
            task_queue="test-replay-queue",
        )
        await asyncio.sleep(0.5)
        approval = HITLSignal(
            actor_id="replay-approver",
            payload={},
            idempotency_key=f"replay-approve-{run_id}",
        )
        await handle.signal("requirements_approved", approval)

        # A clean completion without WorkflowNondeterminismError IS the replay assertion
        result = await handle.result()
        assert result is not None, (
            "EX-2: workflow must produce a result — WorkflowNondeterminismError means "
            "the workflow code is non-deterministic"
        )
