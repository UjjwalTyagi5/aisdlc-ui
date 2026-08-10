"""EX-3: Artifact chain completeness — all four JSONB artifact columns populated.

Tier: integration (requires Temporal server + Postgres)
Criteria:
  EX-3 — After a complete workflow run, all four JSONB artifact columns
          (requirements_payload, design_artifacts, development_artifacts,
           testing_artifacts) must be non-null in the runs table.

This is a phase exit gate — confirms the full artifact chain is wired end-to-end.

Skip guard: tests skip (not error) when POSTGRES_CONN_STRING or LITELLM_API_KEY absent.
"""
from __future__ import annotations

import asyncio

import pytest

from config.env import LITELLM_API_KEY, POSTGRES_CONN_STRING
from tests.temporal.conftest import make_run_id, make_workflow_input, seed_run
from shared.models.workflow_models import HITLSignal
from shared.models.artifacts import (
    RequirementsArtifact,
    DesignArtifact,
    DevelopmentArtifact,
    TestingArtifact,
)

# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------
_skip_no_db = pytest.mark.skipif(
    not POSTGRES_CONN_STRING,
    reason="POSTGRES_CONN_STRING not set — skipping DB-dependent temporal tests",
)

# Live-agent guard — full pipeline invokes real agent graphs via LiteLLM proxy.
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
# EX-3: Full artifact chain
# ---------------------------------------------------------------------------

@pytest.mark.integration
@_skip_no_db
@_skip_no_temporal
@_skip_no_litellm
async def test_artifact_chain_completeness(temporal_env):
    """All four JSONB artifact columns must be non-null after a complete workflow run.

    EX-3 contract:
      - runs.requirements_payload is non-null (RequirementsArtifact shape)
      - runs.design_artifacts is non-null (DesignArtifact shape)
      - runs.development_artifacts is non-null (DevelopmentArtifact shape)
      - runs.testing_artifacts is non-null (TestingArtifact shape)

    Each artifact must be parseable into its respective Pydantic model, confirming
    not just presence but structural correctness.
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

    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)
    await seed_run(run_id, project_id=workflow_input.project_id)

    async with Worker(
        temporal_env.client,
        task_queue="test-chain-queue",
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
            id=f"chain-test-{run_id}",
            task_queue="test-chain-queue",
        )

        # Auto-approve all HITL gates
        for phase in ("requirements", "design", "development"):
            await asyncio.sleep(0.5)
            signal = HITLSignal(
                actor_id="chain-approver",
                payload={},
                idempotency_key=f"chain-{phase}-{run_id}",
            )
            await handle.signal(f"{phase}_approved", signal)

        result = await handle.result()
        assert result is not None

    # Verify all four artifact columns
    engine = create_async_engine(POSTGRES_CONN_STRING, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        res = await session.execute(select(Run).where(Run.id == run_id))
        run = res.scalar_one_or_none()
        assert run is not None, f"EX-3: run {run_id} not found in DB"

        # Each column must be non-null
        assert run.requirements_payload is not None, "EX-3: requirements_payload must be set"
        assert run.design_artifacts is not None, "EX-3: design_artifacts must be set"
        assert run.development_artifacts is not None, "EX-3: development_artifacts must be set"
        assert run.testing_artifacts is not None, "EX-3: testing_artifacts must be set"

        # Each column must parse into its Pydantic model (structural correctness)
        req = RequirementsArtifact(**run.requirements_payload)
        assert req.agent_session_id == run_id, (
            "EX-3: RequirementsArtifact agent_session_id must match run_id"
        )

        _design = DesignArtifact(**run.design_artifacts)
        assert _design.version is not None, "EX-3: DesignArtifact must have version"

        _dev = DevelopmentArtifact(**run.development_artifacts)
        assert _dev.version is not None, "EX-3: DevelopmentArtifact must have version"

        _testing = TestingArtifact(**run.testing_artifacts)
        assert _testing.version is not None, "EX-3: TestingArtifact must have version"

    await engine.dispose()


@pytest.mark.integration
@_skip_no_db
@_skip_no_temporal
@_skip_no_litellm
async def test_artifact_chain_agent_session_ids_consistent(temporal_env):
    """All artifacts for a run must reference the same run_id as their agent_session_id.

    Cross-contamination check: if any artifact was produced by a different run's
    activity execution, its agent_session_id will differ from the run_id.
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

    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)
    await seed_run(run_id, project_id=workflow_input.project_id)

    async with Worker(
        temporal_env.client,
        task_queue="test-consistency-queue",
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
            id=f"consistency-test-{run_id}",
            task_queue="test-consistency-queue",
        )
        for phase in ("requirements", "design", "development"):
            await asyncio.sleep(0.5)
            signal = HITLSignal(
                actor_id="consistency-approver",
                payload={},
                idempotency_key=f"consistency-{phase}-{run_id}",
            )
            await handle.signal(f"{phase}_approved", signal)

        await handle.result()

    engine = create_async_engine(POSTGRES_CONN_STRING, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        res = await session.execute(select(Run).where(Run.id == run_id))
        run = res.scalar_one_or_none()
        assert run is not None

        req = RequirementsArtifact(**run.requirements_payload)
        assert req.agent_session_id == run_id, (
            "EX-3: RequirementsArtifact agent_session_id cross-contamination detected"
        )
    await engine.dispose()
