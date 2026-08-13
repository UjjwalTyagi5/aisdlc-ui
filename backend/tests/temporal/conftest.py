"""Temporal test fixtures for the SDLC pipeline test suite (M5-02).

Provides:
  temporal_env           — WorkflowEnvironment.start_local()  (requires Temporal server)
  temporal_time_skip_env — WorkflowEnvironment.start_time_skipping() (virtual time, no server)
  make_workflow_input    — factory that returns a fresh SDLCWorkflowInput per call
  make_run_id            — helper that mints a distinct uuid4() run_id per call

Skip guards:
  Both env fixtures skip (not error) when the Temporal binary / server is absent,
  keeping the unit tier green before infra is available.
"""
from __future__ import annotations

import uuid
from typing import Generator

import pytest
import pytest_asyncio

from config.env import POSTGRES_CONN_STRING
from shared.models.workflow_models import SDLCWorkflowInput

# ---------------------------------------------------------------------------
# Import probe — skip entire module if temporalio is not installed
# ---------------------------------------------------------------------------
try:
    from temporalio.testing import WorkflowEnvironment
    _TEMPORALIO_AVAILABLE = True
except ImportError:
    _TEMPORALIO_AVAILABLE = False

_skip_no_temporal = pytest.mark.skipif(
    not _TEMPORALIO_AVAILABLE,
    reason="temporalio not installed — skipping Temporal fixture tests",
)

_skip_no_db = pytest.mark.skipif(
    not POSTGRES_CONN_STRING,
    reason="POSTGRES_CONN_STRING not set — skipping DB-dependent temporal tests",
)


# ---------------------------------------------------------------------------
# Run-id / input factories
# ---------------------------------------------------------------------------

def make_run_id() -> str:
    """Return a fresh uuid4 run_id — never shared across calls."""
    return str(uuid.uuid4())


def make_workflow_input(
    run_id: str | None = None,
    project_id: str | None = None,
    tenant_id: str = "test-tenant-001",
    trigger: str = "manual",
) -> SDLCWorkflowInput:
    """Return a fresh SDLCWorkflowInput for each test invocation."""
    return SDLCWorkflowInput(
        run_id=run_id or make_run_id(),
        project_id=project_id or str(uuid.uuid4()),
        tenant_id=tenant_id,
        trigger=trigger,
        agent_version=1,
    )


async def seed_run(
    run_id: str,
    project_id: str | None = None,
    tenant_id: str | None = None,
    temporal_workflow_id: str | None = None,
) -> str:
    """Seed Organization -> Workspace -> Project -> Run so artifact persistence works.

    persist_artifact / sync_run_status update the runs row by id (they do NOT insert);
    in production POST /runs creates the row against an existing project. Integration
    tests that assert on persisted artifacts must seed the FK chain first. Returns run_id.

    temporal_workflow_id: optional — set this so the seeded run passes signals.py's
    Step-2 "has a Temporal workflow" 409 guard and reaches the Step-3 permission
    check (REQ-M7-11 negative-authz tests need to exercise the 403 branch, not 409).
    """
    from sqlalchemy import text as _sa_text

    from shared.db import AsyncSessionFactory
    from shared.models.orm import Organization, Workspace, Project, Run

    tid = uuid.UUID(tenant_id) if tenant_id else uuid.uuid4()
    pid = uuid.UUID(project_id) if project_id else uuid.uuid4()
    rid = uuid.UUID(str(run_id))
    suffix = str(rid)[:8]

    async with AsyncSessionFactory() as session:
        # projects and runs are FORCE-RLS, so the insert must run under the tenant GUC
        # or the WITH CHECK policy rejects it. This used to pass without the GUC only
        # because the app connected as a superuser, which bypasses RLS entirely — the
        # seed was never actually exercising the policy it now has to satisfy.
        # organizations and workspaces are global tables and need no GUC.
        await session.execute(
            _sa_text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(tid)},
        )
        org = Organization(slug=f"canary-org-{suffix}", display_name="Canary Org")
        session.add(org)
        await session.flush()
        ws = Workspace(organization_id=org.id, slug=f"canary-ws-{suffix}", display_name="Canary WS")
        session.add(ws)
        await session.flush()
        proj = Project(id=pid, workspace_id=ws.id, tenant_id=tid, display_name="Canary Project")
        session.add(proj)
        await session.flush()
        run = Run(
            id=rid,
            project_id=pid,
            tenant_id=tid,
            stage="requirements",
            status="running",
            temporal_workflow_id=temporal_workflow_id,
        )
        session.add(run)
        await session.commit()
    return str(run_id)


# ---------------------------------------------------------------------------
# Temporal environment fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def temporal_env():
    """Local Temporal environment for integration tests.

    Starts an embedded Temporal server process. Tests that use this fixture
    are skipped when the temporalio package is absent or the server binary
    cannot be started.
    """
    if not _TEMPORALIO_AVAILABLE:
        pytest.skip("temporalio not installed")
    try:
        async with await WorkflowEnvironment.start_local() as env:
            yield env
    except Exception as exc:
        pytest.skip(f"Temporal local server unavailable: {exc}")


@pytest_asyncio.fixture
async def temporal_time_skip_env():
    """Time-skipping Temporal environment — no real server required.

    Advances virtual time instantly for workflow.sleep / wait_condition
    timeouts.  Used for SLA escalation and rejection tests so they do
    not require a 24-hour wall-clock wait.
    """
    if not _TEMPORALIO_AVAILABLE:
        pytest.skip("temporalio not installed")
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            yield env
    except Exception as exc:
        pytest.skip(f"Temporal time-skipping environment unavailable: {exc}")
