"""Temporal activity for syncing run status to Postgres (M5-06, D-07).

This is the ONLY way SDLCWorkflow mutates the runs table — preserving the
D-06 state-ownership boundary: no direct I/O in the workflow body.

Contract:
  sync_run_status_activity(run_id, status, stage, gate_pending, event_type, tenant_id)
    - Opens a fresh get_db_session_for_tenant/superuser context per call (Assumption A3 —
      never a cached session across Temporal activity retries).
    - Loads the Run row by run_id, applies status/current_stage/gate_pending
      column writes.
    - When event_type is provided, appends an AuditEvent for lifecycle
      transition observability (D-07).
    - Idempotent: repeated calls with the same column values are safe
      (setattr + commit overwrites with the same value).

Supported status transitions (D-07 lifecycle):
  queued -> running -> awaiting_<phase>_approval -> complete

The stage column is written as a human-readable pipeline stage string
(e.g. "requirements", "design", "development", "testing", "complete").
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from temporalio import activity

from shared.db import get_db_session_for_tenant, get_db_session_superuser
from shared.models.orm import AuditEvent, Run
from sqlalchemy import select

_ACTIVITY_NAME = "sync_run_status_activity"


@activity.defn(name=_ACTIVITY_NAME)
async def sync_run_status_activity(
    run_id: str,
    status: str,
    stage: str,
    gate_pending: bool,
    event_type: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> None:
    """Update runs row and optionally append an AuditEvent.

    Args:
        run_id:      UUID string for the pipeline run.
        status:      New value for runs.status (e.g. "running",
                     "awaiting_requirements_approval", "complete").
        stage:       New value for runs.stage and runs.current_stage
                     (e.g. "requirements", "design", "complete").
        gate_pending: Whether a HITL gate is currently blocking the workflow.
        event_type:  When provided, an AuditEvent with this event_type is
                     appended (e.g. "run.workflow_started",
                     "run.activity_completed", "run.gate_reached",
                     "run.workflow_completed").  Pass None to skip audit.
        tenant_id:   Tenant owning this run — passed explicitly from
                     SDLCWorkflowInput.tenant_id because contextvars do not
                     cross Temporal activity boundaries (PITFALLS Integration
                     Gotchas). When provided, uses get_db_session_for_tenant
                     (D-04). Falls back to get_db_session_superuser() only as
                     backward-compat bridge for callers not yet passing tenant_id
                     (D-05 system-scoped path; will fail under FORCE RLS if the
                     app role is not BYPASSRLS — remove fallback after Plan 04).
    """
    # Prefer tenant-scoped session (D-04); superuser only as backward-compat (D-05).
    _ctx = get_db_session_for_tenant(tenant_id) if tenant_id else get_db_session_superuser()
    async with _ctx as session:
        result = await session.execute(select(Run).where(Run.id == run_id))
        run = result.scalar_one_or_none()

        if run is None:
            # Tolerate unknown run_id — activity is idempotent; callers may
            # invoke before the Run row is committed in edge cases.
            return

        # Apply column writes — idempotent by design.
        run.status = status
        run.stage = stage
        run.current_stage = stage
        run.gate_pending = gate_pending

        if event_type is not None:
            # Resolve tenant_id — prefer explicit arg; fall back to the run's tenant;
            # last resort nil UUID so the AuditEvent is never dropped (D-07 observability).
            audit_tenant_id = (
                uuid.UUID(tenant_id) if tenant_id else
                getattr(run, "tenant_id", None) or uuid.UUID(int=0)
            )
            audit = AuditEvent(
                id=uuid.uuid4(),
                tenant_id=audit_tenant_id,
                actor_id="temporal-workflow",
                event_type=event_type,
                resource_type="run",
                resource_id=run_id,
                payload={
                    "status": status,
                    "stage": stage,
                    "gate_pending": gate_pending,
                },
                created_at=datetime.now(timezone.utc),
            )
            session.add(audit)
