"""SC-09: 5 concurrent workflows — independent state, no cross-contamination.

Tier: integration (requires Temporal server + Postgres)
Criteria:
  SC-09 — 5 concurrent workflows run independently; each testing artifact references
           only its own run_id in session context (exercises plan-05 session_id=run_id
           isolation, not a trivial pass).

Skip guard: tests skip (not error) when POSTGRES_CONN_STRING is absent.
The SDLCWorkflow + activity wrappers (plans 03–05) are not yet built; tests xfail.

IMPORTANT — distinct run_ids:
  Each workflow is started with a uuid4() run_id from make_run_id().
  The concurrency test MUST NOT share a hardcoded run_id across workflows.
"""
from __future__ import annotations

import asyncio
import uuid

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

_NUM_CONCURRENT = 5


# ---------------------------------------------------------------------------
# SC-09: Concurrent workflows — independent state
# ---------------------------------------------------------------------------

@pytest.mark.integration
@_skip_no_db
@_skip_no_temporal
@_skip_no_litellm
async def test_concurrent_workflows(temporal_env):
    """5 concurrent workflows must not share state; each testing artifact must reference
    only its own run_id in session context.

    SC-09 / Pitfall 5 contract:
      - The Testing Agent has module-level shared state (output_file, prev_session_id).
      - Plan-05 fixes this by passing session_id=run_id to run_super_agent_async.
      - This test asserts the isolation: each workflow's TestingArtifact records
        its own run_id as agent_session_id, not another workflow's run_id.

    Each workflow is started with a DISTINCT uuid4() run_id (from make_run_id()),
    so this is NOT a trivial pass — cross-contamination would manifest as mismatched
    agent_session_ids.
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

    # Each workflow gets a DISTINCT run_id — never shared
    run_ids = [make_run_id() for _ in range(_NUM_CONCURRENT)]
    workflow_inputs = [make_workflow_input(run_id=rid) for rid in run_ids]
    # Seed the FK chain for each distinct run so artifact persistence + isolation checks resolve.
    for _inp in workflow_inputs:
        await seed_run(_inp.run_id, project_id=_inp.project_id)

    all_activities = [
        run_requirements_activity,
        run_design_activity,
        run_development_activity,
        run_testing_activity,
        emit_escalation_activity,
        sync_run_status_activity,
    ]

    async with Worker(
        temporal_env.client,
        task_queue="test-concurrent-queue",
        workflows=[SDLCWorkflow],
        activities=all_activities,
    ):
        # Start all 5 workflows simultaneously
        handles = await asyncio.gather(*[
            temporal_env.client.start_workflow(
                SDLCWorkflow.run,
                inp,
                id=f"concurrent-{inp.run_id}",
                task_queue="test-concurrent-queue",
            )
            for inp in workflow_inputs
        ])

        # Auto-approve all HITL gates for all workflows
        async def approve_all(handle, run_id: str) -> None:
            for phase in ("requirements", "design", "development"):
                await asyncio.sleep(0.3)
                signal = HITLSignal(
                    actor_id="auto-approver",
                    payload={},
                    idempotency_key=f"auto-{phase}-{run_id}",
                )
                await handle.signal(f"{phase}_approved", signal)

        await asyncio.gather(*[
            approve_all(h, rid) for h, rid in zip(handles, run_ids)
        ])

        # Wait for all workflows to complete
        results = await asyncio.gather(*[h.result() for h in handles])
        assert all(r is not None for r in results), (
            "SC-09: all 5 concurrent workflows must complete"
        )

    # Verify no cross-contamination: each testing artifact references only its own run_id
    engine = create_async_engine(POSTGRES_CONN_STRING, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        for run_id in run_ids:
            result = await session.execute(select(Run).where(Run.id == run_id))
            run = result.scalar_one_or_none()
            assert run is not None, f"SC-09: run {run_id} not found in DB"
            assert run.testing_artifacts is not None, (
                f"SC-09: run {run_id} testing_artifacts must be set"
            )
            # The testing artifact must reference only its own run_id (isolation check)
            artifact_session_id = run.testing_artifacts.get("agent_session_id", "")
            assert artifact_session_id == run_id, (
                f"SC-09: run {run_id} testing artifact has agent_session_id="
                f"{artifact_session_id!r} — cross-contamination detected (Pitfall 5)"
            )
    await engine.dispose()


@pytest.mark.integration
@_skip_no_db
@_skip_no_temporal
@_skip_no_litellm
async def test_concurrent_workflows_distinct_run_ids():
    """Verify the concurrency test itself uses distinct run_ids — meta-test for the fixture.

    This test confirms that make_run_id() returns unique values and that the
    concurrent test is not trivially passing because all workflows share the same run_id.
    """
    ids = {make_run_id() for _ in range(_NUM_CONCURRENT)}
    assert len(ids) == _NUM_CONCURRENT, (
        "make_run_id() must return distinct values on each call"
    )
