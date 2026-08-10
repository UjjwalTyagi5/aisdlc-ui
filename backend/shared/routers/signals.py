"""Signals router — real Temporal signal dispatch (replaces M4 stub).

Exposes POST /runs/{run_id}/signals/{name} which authorizes the request per
D-08/D-01 (tenant ownership + phase-specific PERMISSION) BEFORE dispatching
the signal to the running Temporal workflow via the cached
app.state.temporal_client.

D-01: migrated from the legacy M5 "roles" claim + local phase-approver-role map
to the M7.2 "permissions" claim + shared _PHASE_PERMISSION map (single source
of truth shared with the FastAPI require_permission dependency layer, no dual
maintenance). The 403-before-handle ordering contract is UNCHANGED (REQ-M7-11).

Authorization order (all checks BEFORE any handle is obtained):
  1. Tenant-scoped _get_run_or_404 — 404 on cross-tenant access (T-M5-18).
  2. 409 when run.temporal_workflow_id is absent (no Temporal workflow yet).
  3. Phase-permission check — 403 if actor lacks the required permission for
     the current phase (T-7.2-14/T-M5-19). handle.signal is NOT called for a
     permission-lacking actor (REQ-M7-11, Pitfall 4).

Signal name mapping:
  "hitl.decision" + payload.decision="approved" -> <phase>_approved
  "hitl.decision" + payload.decision="rejected" -> <phase>_rejected
  Any other name  -> passed through to the workflow as-is.

Idempotency: idempotency_key defaults to uuid4() when absent; carried into
the HITLSignal payload so the workflow can deduplicate (T-M5-20).

Routes (absolute paths — registered without a router prefix):
  POST /runs/{run_id}/signals/{name}  — dispatch + AuditEvent

All routes are JWT-protected (NOT in _EXEMPT_PATHS).
Security: T-M5-18 (cross-tenant spoofing), T-M5-19 (role elevation),
          T-M5-20 (signal replay), T-M5-23 (per-request Client.connect avoided).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.audit.models import AuditEventPayload
from shared.audit.service import audit_service
from shared.authz.permissions import _PHASE_PERMISSION, has_permission
from shared.db import get_db_session
from shared.models.orm import AuditEvent, Run
from shared.models.workflow_models import HITLSignal
from shared.routers._schemas import SignalAckOut

logger = logging.getLogger(__name__)

signals_router = APIRouter()

# ── Signal name -> phase derivation ─────────────────────────────────────────
# hitl.decision signals encode phase via the run's current stage.
# Phase-specific signal names (requirements_approved, design_rejected, …)
# encode the phase directly in the signal name.
# Derived from the shared _PHASE_PERMISSION map (D-01) — single source of truth,
# no dual maintenance between the FastAPI dependency layer and the signal handler.
_PHASE_SIGNAL_PREFIXES = tuple(_PHASE_PERMISSION.keys())


def _derive_phase_from_signal(signal_name: str, run_stage: Optional[str]) -> Optional[str]:
    """Return the pipeline phase implied by a signal name.

    For "hitl.decision" the phase is the run's current_stage / stage.
    For phase-specific names like "requirements_approved" the phase is the prefix.
    """
    if signal_name in ("hitl.decision", "within_agent_clarification"):
        return run_stage or "requirements"
    for prefix in _PHASE_SIGNAL_PREFIXES:
        if signal_name.startswith(prefix + "_"):
            return prefix
    return None


def _map_signal_name(signal_name: str, payload: dict, run_stage: Optional[str]) -> str:
    """Map the inbound signal name to the workflow @workflow.signal method name.

    "hitl.decision" with payload.decision="approved" -> <phase>_approved
    "hitl.decision" with payload.decision="rejected" -> <phase>_rejected
    Any other name -> returned unchanged (must match a @workflow.signal handler).
    """
    if signal_name != "hitl.decision":
        return signal_name
    phase = run_stage or "requirements"
    decision = (payload.get("decision") or "approved").lower()
    if decision == "rejected":
        return f"{phase}_rejected"
    return f"{phase}_approved"


def _check_permission_for_phase(request_state: object, phase: str) -> bool:
    """Return True if the actor holds the approval permission for the given phase.

    Permissions are read from request.state.permissions (list of strings injected
    by the JWT middleware from the "permissions" claim, D-01/D-02). The signal
    route keeps this in-body check — unlike fixed-permission routes — because the
    required permission is PHASE-DERIVED at runtime (the phase comes from the run's
    current stage / the signal name), not a static string a require_permission(...)
    factory dependency could be parameterized with ahead of time (Pattern 4).

    A missing or empty "permissions" claim means the actor has no approval rights
    (deny-by-default). An unknown phase also denies (fail-closed unchanged).
    admin:* passes via the shared has_permission wildcard (D-01 single model).
    """
    actor_permissions: list[str] = getattr(request_state, "permissions", []) or []
    required_permission = _PHASE_PERMISSION.get(phase)
    if not required_permission:
        # Unknown phase — deny by default (fail-closed security posture)
        return False
    return has_permission(actor_permissions, required_permission)


class SignalIn(BaseModel):
    """Request body for signal dispatch."""
    payload: Optional[dict] = None
    idempotencyKey: str = ""


@signals_router.post("/runs/{run_id}/signals/{name}", response_model=SignalAckOut)
async def send_signal(
    run_id: str,
    name: str,
    body: SignalIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Dispatch a named signal to a running Temporal workflow (D-08/D-01).

    Authorization order (D-08/D-01 — all checks BEFORE any Temporal call):
      1. Tenant-scoped 404 guard (T-M5-18).
      2. 409 if run has no Temporal workflow.
      3. 403 if actor lacks the required PERMISSION for the phase (T-7.2-14,
         REQ-M7-11). handle.signal is NOT called for a permission-lacking actor.
      4. Map signal name + dispatch via cached client (T-M5-23).
      5. Append REAL AuditEvent (stub=False).

    REQ-M10-05: "within_agent_clarification" reuses this generic signal path
    unchanged — _derive_phase_from_signal resolves it to the run's current
    stage (same as "hitl.decision") and _map_signal_name passes the name
    through verbatim to the @workflow.signal handler of the same name
    (milestone-10.2-01). The clarification answer travels in
    body.payload (`{clarification_id, answer}`), which the workflow handler
    reads to construct a ClarificationAnswer.
    """
    tenant_id = getattr(request.state, "tenant_id", "")
    idempotency_key = body.idempotencyKey or str(uuid.uuid4())

    # ── Step 1: Tenant-scoped lookup (T-M5-18) ────────────────────────────────
    result = await db.execute(
        select(Run).where(
            Run.id == run_id,
            Run.tenant_id == tenant_id,
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    # ── Step 2: Temporal workflow existence check ─────────────────────────────
    temporal_workflow_id = run.temporal_workflow_id
    if not temporal_workflow_id:
        raise HTTPException(
            status_code=409,
            detail="Run has no associated Temporal workflow — start the run first",
        )

    # ── Step 3: Phase-permission authorization (D-01, REQ-M7-11, T-7.2-14) ───
    # Phase derived from signal name + current run stage BEFORE any handle is
    # obtained — the permission check below MUST precede get_workflow_handle
    # (Pitfall 4 / T-7.2-14): a forged token can never reach the Temporal client.
    run_stage = run.current_stage or run.stage or "requirements"
    phase = _derive_phase_from_signal(name, run_stage)
    if phase is None:
        # Signal name has no discernible phase — deny (fail-closed)
        raise HTTPException(
            status_code=422,
            detail=f"Cannot determine pipeline phase for signal '{name}'",
        )

    if not _check_permission_for_phase(request.state, phase):
        # 403 returned BEFORE any handle is obtained or signal dispatched
        # (REQ-M7-11). Generic message — no permission-name leak (T-7.2-11).
        raise HTTPException(
            status_code=403,
            detail="Forbidden: actor lacks the required approval permission for this phase",
        )

    # ── Step 4: Map signal name + dispatch via cached client (T-M5-23) ────────
    workflow_signal_name = _map_signal_name(name, body.payload or {}, run_stage)
    client = request.app.state.temporal_client
    handle = client.get_workflow_handle(temporal_workflow_id)
    actor_id = getattr(request.state, "user_id", "system")

    # ── Step 4b: Blocking AuditEvent before signal (D-06, T-M8-09) ───────────
    # emit_blocking MUST succeed before handle.signal() is called — if the audit
    # write fails the signal is NOT dispatched (repudiation mitigation T-M8-09).
    # asyncio.wait_for(timeout=30) guards against HITL deadlock (Pitfall 7 / T-M8-10).
    decision = (body.payload or {}).get("decision", "approved")
    event_type = "hitl_rejection" if decision == "rejected" else "hitl_approval"
    try:
        await asyncio.wait_for(
            audit_service.emit_blocking(
                AuditEventPayload(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    event_type=event_type,
                    agent_type="temporal",
                    actor_id=actor_id,
                    payload={
                        "signal_name": name,
                        "phase": phase,
                        "decision": decision,
                        "idempotency_key": idempotency_key,
                    },
                )
            ),
            timeout=30.0,
        )
    except (asyncio.TimeoutError, Exception) as exc:
        logger.error(
            "HITL audit emit failed: %s — signal NOT dispatched",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="Audit service unavailable — signal not accepted",
        )

    # ── Step 5: Signal dispatch (ONLY after blocking audit emit succeeds) ─────
    await handle.signal(
        workflow_signal_name,
        HITLSignal(
            actor_id=actor_id,
            payload=body.payload or {},
            idempotency_key=idempotency_key,
        ),
    )

    logger.info(
        "signal dispatched: run_id=%r signal=%r -> %r phase=%r idempotency_key=%r tenant=%r",
        run_id,
        name,
        workflow_signal_name,
        phase,
        idempotency_key,
        tenant_id,
    )

    return SignalAckOut(
        accepted=True,
        signalName=name,
        runId=run_id,
        idempotencyKey=idempotency_key,
    )
