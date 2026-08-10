"""SC-01, SC-02, SC-05: Workflow start, requirements persistence, full pipeline.

Tier: integration (requires Temporal server + Postgres)
Criteria:
  SC-01 — POST /runs returns run_id + temporal_workflow_id
  SC-02 — Requirements activity persists artifact to runs.requirements_payload
  SC-05 — Full pipeline Requirements→Design→Development→Testing completes

Skip guard: tests skip (not error) when POSTGRES_CONN_STRING is absent.
The SDLCWorkflow, activity wrappers, and POST /runs endpoint (plans 03/04/06) are not
yet built; tests xfail until they land.
"""
from __future__ import annotations

import uuid

import pytest

from config.env import POSTGRES_CONN_STRING, LITELLM_API_KEY
from tests.temporal.conftest import make_run_id, make_workflow_input

# Live-agent guard — workflow execution invokes the real agent graphs through the
# LiteLLM proxy. Skip (don't fail) when the proxy isn't configured.
_skip_no_litellm = pytest.mark.skipif(
    not LITELLM_API_KEY,
    reason="LITELLM_API_KEY not set — litellm-proxy not configured; skipping live-agent temporal tests",
)

# ---------------------------------------------------------------------------
# Skip guard — integration tests require infra
# ---------------------------------------------------------------------------
_skip_no_db = pytest.mark.skipif(
    not POSTGRES_CONN_STRING,
    reason="POSTGRES_CONN_STRING not set — skipping DB-dependent temporal tests",
)

# Import probe for temporalio
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
# SC-01: POST /runs returns run_id + temporal_workflow_id
# ---------------------------------------------------------------------------

@pytest.mark.integration
@_skip_no_db
@_skip_no_temporal
@_skip_no_litellm
async def test_workflow_start_and_complete(temporal_env):
    """POST /runs must create a Run row and start a Temporal workflow.

    SC-01: response contains run_id (UUID) and temporal_workflow_id (non-empty string).
    """
    import httpx
    from httpx import AsyncClient, ASGITransport
    import process_api  # type: ignore[import]

    project_id = str(uuid.uuid4())
    tenant_id = "test-tenant-001"

    async with AsyncClient(
        transport=ASGITransport(app=process_api.app),  # type: ignore[attr-defined]
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/runs",
            json={"project_id": project_id, "trigger": "manual"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"
        data = response.json()
        assert "runId" in data, "SC-01: response must contain runId"
        assert "temporalWorkflowId" in data, "SC-01: response must contain temporalWorkflowId"
        assert data["runId"], "runId must be non-empty"
        assert data["temporalWorkflowId"], "temporalWorkflowId must be non-empty"


# ---------------------------------------------------------------------------
# SC-02: Requirements artifact persisted
# ---------------------------------------------------------------------------

@pytest.mark.integration
@_skip_no_db
@_skip_no_temporal
@_skip_no_litellm
async def test_requirements_activity_persists(temporal_env):
    """After the requirements activity completes, runs.requirements_payload must be non-null.

    SC-02: artifact persisted to DB with correct run_id binding.
    """
    from workflows.sdlc_workflow import SDLCWorkflow  # type: ignore[import]
    from workflows.activities.requirements_activity import run_requirements_activity  # type: ignore[import]

    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from sqlalchemy import select
    from shared.models.orm import Run  # type: ignore[import]

    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)

    async with Worker(
        temporal_env.client,
        task_queue="test-persist-queue",
        workflows=[SDLCWorkflow],
        activities=[run_requirements_activity],
    ):
        handle = await temporal_env.client.start_workflow(
            SDLCWorkflow.run,
            workflow_input,
            id=f"persist-test-{run_id}",
            task_queue="test-persist-queue",
        )
        await handle.result()

    # Verify artifact persisted to DB
    engine = create_async_engine(POSTGRES_CONN_STRING, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        result = await session.execute(select(Run).where(Run.id == run_id))
        run = result.scalar_one_or_none()
        assert run is not None, f"Run {run_id} not found in DB"
        assert run.requirements_payload is not None, (
            "SC-02: runs.requirements_payload must be populated after requirements activity"
        )
    await engine.dispose()


# ---------------------------------------------------------------------------
# SC-05: Full pipeline
# ---------------------------------------------------------------------------

@pytest.mark.integration
@_skip_no_db
@_skip_no_temporal
@_skip_no_litellm
async def test_full_pipeline(temporal_env):
    """Full pipeline Requirements→Design→Development→Testing completes without error.

    SC-05: all four artifact columns populated in DB after workflow completion.
    """
    from workflows.sdlc_workflow import SDLCWorkflow  # type: ignore[import]
    from workflows.activities.requirements_activity import run_requirements_activity  # type: ignore[import]
    from workflows.activities.design_activity import run_design_activity  # type: ignore[import]
    from workflows.activities.development_activity import run_development_activity  # type: ignore[import]
    from workflows.activities.testing_activity import run_testing_activity  # type: ignore[import]
    from workflows.activities.emit_escalation_activity import emit_escalation_activity  # type: ignore[import]
    from shared.models.workflow_models import HITLSignal

    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from sqlalchemy import select
    from shared.models.orm import Run  # type: ignore[import]

    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)

    async with Worker(
        temporal_env.client,
        task_queue="test-pipeline-queue",
        workflows=[SDLCWorkflow],
        activities=[
            run_requirements_activity,
            run_design_activity,
            run_development_activity,
            run_testing_activity,
            emit_escalation_activity,
        ],
    ):
        handle = await temporal_env.client.start_workflow(
            SDLCWorkflow.run,
            workflow_input,
            id=f"pipeline-test-{run_id}",
            task_queue="test-pipeline-queue",
        )

        # Auto-approve each HITL gate to drive the pipeline through all phases
        for phase in ("requirements", "design", "development"):
            import asyncio
            await asyncio.sleep(0.5)
            signal = HITLSignal(
                actor_id="auto-approver",
                payload={},
                idempotency_key=f"auto-{phase}-{run_id}",
            )
            await handle.signal(f"{phase}_approved", signal)

        result = await handle.result()
        assert result is not None

    # Verify all four artifact columns populated
    engine = create_async_engine(POSTGRES_CONN_STRING, poolclass=NullPool)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        res = await session.execute(select(Run).where(Run.id == run_id))
        run = res.scalar_one_or_none()
        assert run is not None
        assert run.requirements_payload is not None, "SC-05: requirements_payload must be set"
        assert run.design_artifacts is not None, "SC-05: design_artifacts must be set"
        assert run.development_artifacts is not None, "SC-05: development_artifacts must be set"
        assert run.testing_artifacts is not None, "SC-05: testing_artifacts must be set"
    await engine.dispose()
