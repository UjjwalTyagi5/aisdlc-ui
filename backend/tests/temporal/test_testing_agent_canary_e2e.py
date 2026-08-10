"""SC-08: Testing agent canary — mandatory P0 exit gate.

Tier: integration (requires Temporal server + Postgres + LiteLLM proxy)
Criteria:
  SC-08 — Testing agent canary: workflow reaches testing phase, run_testing_activity
           returns a populated TestingArtifact, runs.testing_artifacts is non-null.

This is the mandatory P0 gate — M5 cannot seal without this test passing.

Skip guard: tests skip (not error) when POSTGRES_CONN_STRING or LITELLM_API_KEY absent.
"""
from __future__ import annotations

import asyncio

import pytest

from config.env import LITELLM_API_KEY, POSTGRES_CONN_STRING
from tests.temporal.conftest import make_run_id, make_workflow_input, seed_run
from shared.models.workflow_models import HITLSignal

# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------
_skip_no_db = pytest.mark.skipif(
    not POSTGRES_CONN_STRING,
    reason="POSTGRES_CONN_STRING not set — skipping DB-dependent temporal tests",
)

# Live-agent guard — canary invokes all 4 real agent graphs via LiteLLM proxy.
_skip_no_litellm = pytest.mark.skipif(
    not LITELLM_API_KEY,
    reason="LITELLM_API_KEY not set — litellm-proxy not configured; skipping live-agent temporal tests",
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


# ---------------------------------------------------------------------------
# SC-08: Testing agent canary (mandatory P0)
# ---------------------------------------------------------------------------

@pytest.mark.integration
@_skip_no_db
@_skip_no_temporal
@_skip_no_litellm
async def test_testing_agent_canary_e2e(temporal_env):
    """End-to-end canary: SDLCWorkflow reaches the testing phase and produces a TestingArtifact.

    SC-08 assertions:
      1. SDLCWorkflow completes requirements + design + development activities without error
      2. run_testing_activity returns a non-empty TestingArtifact
      3. runs.testing_artifacts is non-null in Postgres after workflow completion
      4. (Manual, not asserted here) Temporal Web UI shows testing activity in history

    This is the mandatory P0 gate — the phase cannot seal without this test passing.
    """
    from workflows.sdlc_workflow import SDLCWorkflow  # type: ignore[import]
    from workflows.activities.requirements_activity import run_requirements_activity  # type: ignore[import]
    from workflows.activities.design_activity import run_design_activity  # type: ignore[import]
    from workflows.activities.development_activity import run_development_activity  # type: ignore[import]
    from workflows.activities.testing_activity import run_testing_activity  # type: ignore[import]
    from workflows.activities.emit_escalation_activity import emit_escalation_activity  # type: ignore[import]
    from workflows.activities.sync_status_activity import sync_run_status_activity  # type: ignore[import]

    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from sqlalchemy import select
    from shared.models.orm import Run  # type: ignore[import]
    from shared.models.artifacts import TestingArtifact

    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)

    # Seed the FK chain (Org->Workspace->Project->Run) so the activities' persist
    # path (UPDATE by run_id) populates runs.testing_artifacts — production does
    # this via POST /runs against an existing project.
    await seed_run(run_id, project_id=workflow_input.project_id)

    async with Worker(
        temporal_env.client,
        task_queue="test-canary-queue",
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
        handle = await temporal_env.client.start_workflow(
            SDLCWorkflow.run,
            workflow_input,
            id=f"canary-e2e-{run_id}",
            task_queue="test-canary-queue",
        )

        # Auto-approve all three inter-agent HITL gates
        for phase in ("requirements", "design", "development"):
            await asyncio.sleep(0.5)
            signal = HITLSignal(
                actor_id="canary-approver",
                payload={},
                idempotency_key=f"canary-{phase}-{run_id}",
            )
            await handle.signal(f"{phase}_approved", signal)

        result = await handle.result()
        assert result is not None, "SC-08: canary workflow must complete"

    # Assert 1: run_testing_activity must return a populated TestingArtifact
    # (verified via DB column — the activity persists before returning)
    engine = create_async_engine(POSTGRES_CONN_STRING, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        res = await session.execute(select(Run).where(Run.id == run_id))
        run = res.scalar_one_or_none()

        # Assert 2: runs.testing_artifacts must be non-null (SC-08)
        assert run is not None, f"SC-08: run {run_id} not found in DB"
        assert run.testing_artifacts is not None, (
            "SC-08: runs.testing_artifacts must be non-null after canary workflow completes"
        )

        # Assert 3: testing artifact must be a valid TestingArtifact shape
        artifact = TestingArtifact(**run.testing_artifacts)
        assert artifact.version is not None, (
            "SC-08: TestingArtifact must have a version field set"
        )

    await engine.dispose()
