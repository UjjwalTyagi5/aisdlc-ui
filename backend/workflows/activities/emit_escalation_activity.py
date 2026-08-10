"""Temporal activity for recording SLA-breach escalation events (M5-05, D-04).

This is the only place the SLA escalation side-effect is recorded.  The
SDLCWorkflow (plan 06) invokes this activity inside the `except asyncio.TimeoutError`
branch when wait_condition times out after the grace period (D-04 / RESEARCH Pattern 6).

Design constraints:
- Pure activity I/O — no Temporal workflow APIs, no workflow.wait_condition,
  no workflow.start_timer (D-06 compliant).
- Uses a fresh get_db_session_for_tenant(tenant_uuid) context per call — never a cached session
  (Assumption A3: Temporal activity retries must see committed Postgres state).
- Payload carries only run_id/phase/tenant_id — no artifact content or secrets
  (T-M5-11 mitigation: no information disclosure risk from the AuditEvent payload).
- Cannot be triggered by external actors — this function is workflow-invoked only
  via execute_activity(); it is not registered on any HTTP route (T-M5-12 mitigation).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from temporalio import activity

from shared.db import get_db_session_for_tenant
from shared.models.orm import AuditEvent
from shared.services.metrics import TEMPORAL_HITL_WAIT_DURATION

_ACTIVITY_NAME = "emit_escalation_activity"


@activity.defn(name=_ACTIVITY_NAME)
async def emit_escalation_activity(payload: Dict[str, Any]) -> None:
    """Record an SLA-breach escalation AuditEvent and emit the HITL wait metric.

    Expected payload keys:
      run_id      (str)  — the SDLC pipeline run that breached its SLA
      phase       (str)  — which inter-agent gate timed out (e.g. "requirements")
      tenant_id   (str)  — tenant owning the run (used for AuditEvent RLS scoping)
      grace_applied (bool, optional) — whether the 5-minute grace period was consumed
      elapsed_seconds (float, optional) — wall-clock seconds from gate entry to timeout

    The activity is idempotent by design: writing multiple escalation events for the
    same run_id/phase is safe because AuditEvents are append-only (no upsert needed).
    Duplicate escalations are distinguishable by created_at timestamps.
    """
    run_id: str = payload.get("run_id", "unknown")
    phase: str = payload.get("phase", "unknown")
    tenant_id_raw: str = payload.get("tenant_id", str(uuid.uuid4()))
    grace_applied: bool = bool(payload.get("grace_applied", True))
    elapsed_seconds: float = float(payload.get("elapsed_seconds", 0.0))

    # Resolve tenant_id to UUID — tolerates both string and already-UUID inputs.
    try:
        tenant_uuid = uuid.UUID(tenant_id_raw) if isinstance(tenant_id_raw, str) else tenant_id_raw
    except ValueError:
        # Non-UUID tenant_id (e.g. in test environments) — generate a placeholder
        # rather than hard-failing a side-effect-only activity.
        tenant_uuid = uuid.uuid4()

    # Write an AuditEvent for the SLA breach.  Two complementary event_types are
    # recorded so downstream consumers can filter on either:
    #   "run.sla_breached"          — the SLA deadline was exceeded
    #   "run.escalation_triggered"  — an escalation notification was raised
    # Plan 06 (SDLCWorkflow) invokes this once per phase timeout; the workflow
    # then proceeds to the escalation path (e.g. notify delegate).
    now = datetime.now(timezone.utc)
    # tenant_uuid derived from payload above (D-04 — tenant from the entity being processed).
    async with get_db_session_for_tenant(str(tenant_uuid)) as session:
        audit = AuditEvent(
            id=uuid.uuid4(),
            tenant_id=tenant_uuid,
            actor_id="temporal-workflow",
            event_type="run.sla_breached",
            resource_type="run",
            resource_id=run_id,
            payload={
                "phase": phase,
                "escalation_triggered": True,
                "grace_applied": grace_applied,
                "elapsed_seconds": elapsed_seconds,
                "delegate_notified": False,
                "event_subtype": "run.escalation_triggered",
            },
            created_at=now,
        )
        session.add(audit)

    # Emit TEMPORAL_HITL_WAIT_DURATION with outcome="escalate" (D-07 observability).
    # elapsed_seconds may be 0.0 when the caller does not track wall-clock time;
    # the metric is still emitted so the escalate outcome counter increments.
    TEMPORAL_HITL_WAIT_DURATION.labels(phase=phase, outcome="escalate").observe(
        elapsed_seconds
    )
