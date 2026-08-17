"""Cross-run pending-gate queue — GET /approvals, GET /approvals/metrics.

A *gate* is a run paused for a human. There is no approvals table: the run row
itself carries the state (`gate_pending` + `current_stage`), so a gate is derived,
not stored, and cannot drift from the run it belongs to.

Only approval gates exist here. Clarifications are run state rather
than a column, so `?type=clarification` correctly returns nothing today instead of
inventing rows — the queue shows what the database can actually prove is waiting.

`waitingForRole` is deliberately NOT returned. The phase→owning-role matrix lives
in the frontend (lib/roles.ts AGENT_OWNERSHIP) and is presentation, not authorization;
duplicating it in Python would give it two homes and one of them would go stale. The
BFF seam adds it from that matrix. What this endpoint DOES own is `requiredPermission`,
which is authorization and is resolved from the same _PHASE_PERMISSION map the signal
handler enforces with.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import Field

from shared.authz.audit import record_rbac_change
from shared.authz.dependency import require_permission
from shared.authz.permissions import _PHASE_PERMISSION
from shared.authz.read_scope import allowed_workspace_ids
from shared.db import get_db_session
from shared.services import approval_requests as approval_service
from shared.services.approval_requests import ApprovalError

logger = logging.getLogger(__name__)

approvals_router = APIRouter(prefix="/approvals")

# The backend's phase vocabulary says "code_review"; the frontend Phase enum says
# "review". Translated at the boundary rather than in the UI so exactly one place
# knows the two spellings.
_PHASE_TO_UI = {"code_review": "review"}

# Sign-offs that no owning role, and no Project Admin fallback, may waive.
_MANDATORY_PHASES = {"security", "deployment"}


class GateArtifactRef(BaseModel):
    id: str
    title: str
    type: str


class ApprovalGateOut(BaseModel):
    id: str
    type: str
    runId: str
    projectId: str
    projectName: str
    phase: str
    agentType: str
    requiredPermission: str
    capabilityClass: str
    mandatory: bool
    title: str
    summary: str
    requestedBy: str
    requestedAt: str
    deadline: Optional[str] = None
    artifact: Optional[GateArtifactRef] = None
    question: Optional[str] = None


class ApprovalQueueMetricsOut(BaseModel):
    approvals: int
    clarifications: int
    oldestMinutes: int
    generatedAt: str


async def _pending_gates(db: AsyncSession, request: Request) -> list[ApprovalGateOut]:
    allowed = await allowed_workspace_ids(db, request)
    scoped = allowed is not None
    clause = " AND p.workspace_id = ANY(CAST(:ws AS uuid[]))" if scoped else ""

    rows = (await db.execute(
        text(
            "SELECT r.id, r.current_stage, r.updated_at, r.trigger, "
            "       p.id AS project_id, p.display_name AS project_name "
            "FROM runs r JOIN projects p ON p.id = r.project_id "
            "WHERE r.gate_pending = true AND r.current_stage IS NOT NULL"
            + clause +
            # Oldest first — SLA pressure rises to the top of the queue.
            " ORDER BY r.updated_at ASC"
        ),
        {"ws": allowed or []},
    )).fetchall()

    gates: list[ApprovalGateOut] = []
    for r in rows:
        stage = r.current_stage
        ui_phase = _PHASE_TO_UI.get(stage, stage)
        permission = _PHASE_PERMISSION.get(stage)
        if permission is None:
            # A stage with no approval permission is not a human gate. Skipping it
            # is safer than inventing a permission nobody can hold, which would
            # park an unactionable row in every queue forever.
            logger.warning("run %s has gate_pending at unknown stage %r — skipped", r.id, stage)
            continue
        gates.append(ApprovalGateOut(
            id=f"{r.id}:{ui_phase}",
            type="approval",
            runId=str(r.id),
            projectId=str(r.project_id),
            projectName=r.project_name,
            phase=ui_phase,
            agentType=ui_phase,
            requiredPermission=permission,
            capabilityClass="consequential",
            mandatory=stage in _MANDATORY_PHASES,
            title=f"{ui_phase.replace('_', ' ').title()} awaiting approval",
            summary=f"{r.project_name} — the {ui_phase.replace('_', ' ')} stage is paused for a decision.",
            # Always "agent": a gate is raised by the agent that finished the stage,
            # never by the person who started the run. run.trigger records how the
            # RUN began, which is a different question and not the one asked here.
            requestedBy="agent",
            requestedAt=r.updated_at.astimezone(timezone.utc).isoformat(),
        ))
    return gates


@approvals_router.get(
    "",
    response_model=list[ApprovalGateOut],
    dependencies=[Depends(require_permission("artifact:view"))],
)
async def list_gates(
    request: Request,
    type: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
) -> list[ApprovalGateOut]:
    gates = await _pending_gates(db, request)
    if type == "clarification":
        return []
    if type == "approval":
        return [g for g in gates if g.type == "approval"]
    return gates


# ── approval REQUESTS ────────────────────────────────────────────────────────
# Distinct from the derived gates above, and the distinction is not cosmetic. A gate
# is a run paused for a human: it has no initiator, because the agent produced it. A
# request is raised BY someone, which is what makes self-approval expressible — and
# therefore blockable. They share this URL space because both are "things waiting on a
# person", and the queue UI shows them together.


class ApprovalRequestIn(BaseModel):
    subjectKind: str = Field(min_length=1, max_length=32)
    subjectId: Optional[str] = None
    title: str = Field(min_length=1, max_length=255)
    detail: Optional[str] = None
    targetRole: str = Field(min_length=1, max_length=64)
    scopeKind: str = Field(pattern="^(organization|business_unit|project|workstream)$")
    scopeId: str
    requestType: str = Field(default="standard", pattern="^(standard|specialist_required)$")


class DecisionIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=2000)


def _http(exc: ApprovalError) -> HTTPException:
    """Map a service error to its HTTP shape, preserving the machine-readable code.

    The code is what a client branches on; the message is for the person reading it.
    Returning only a message would force clients to match on prose.
    """
    return HTTPException(
        status_code=exc.http_status, detail={"error": exc.code, "message": str(exc)}
    )


@approvals_router.post(
    "/requests",
    status_code=201,
    dependencies=[Depends(require_permission("artifact:view"))],
)
async def create_approval_request(
    request: Request,
    body: ApprovalRequestIn,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Raise a request. The initiator is taken from the session, never from the body.

    Accepting an initiator from the caller would let anyone raise a request in someone
    else's name — and then approve it themselves without tripping the self-approval
    rule, because the recorded initiator would be a different person.
    """
    tenant_id = getattr(request.state, "tenant_id", "") or ""
    try:
        return await approval_service.create_request(
            db,
            tenant_id=tenant_id,
            initiator_id=getattr(request.state, "user_id", "") or "",
            subject_kind=body.subjectKind,
            subject_id=body.subjectId,
            title=body.title,
            detail=body.detail,
            target_role=body.targetRole,
            scope_kind=body.scopeKind,
            scope_id=body.scopeId,
            request_type=body.requestType,
        )
    except ApprovalError as exc:
        raise _http(exc)


@approvals_router.get(
    "/requests",
    dependencies=[Depends(require_permission("artifact:view"))],
)
async def list_approval_requests(
    request: Request,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    return await approval_service.list_requests(db, status=status)


@approvals_router.post(
    "/{request_id}/approve",
    dependencies=[Depends(require_permission("approve"))],
)
async def approve_request(
    request_id: str,
    request: Request,
    body: DecisionIn,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    return await _decide(request_id, request, body, db, decision="approved")


@approvals_router.post(
    "/{request_id}/reject",
    dependencies=[Depends(require_permission("approve"))],
)
async def reject_request(
    request_id: str,
    request: Request,
    body: DecisionIn,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    return await _decide(request_id, request, body, db, decision="rejected")


async def _decide(
    request_id: str,
    request: Request,
    body: DecisionIn,
    db: AsyncSession,
    *,
    decision: str,
) -> dict:
    """Thin pass-through. Every rule that matters is in the service, including the
    self-approval block, so a second caller cannot reach the operation without it."""
    try:
        result = await approval_service.decide(
            db,
            request_id=request_id,
            decider_id=getattr(request.state, "user_id", "") or "",
            decision=decision,
            reason=body.reason,
        )
    except ApprovalError as exc:
        raise _http(exc)

    await record_rbac_change(
        db,
        tenant_id=getattr(request.state, "tenant_id", "") or "",
        actor_id=result["decidedBy"],
        event_type=f"approval.request.{decision}",
        subject_id=result["initiatorId"],
        scope_kind=result["scopeKind"],
        scope_id=result["scopeId"],
        role=result["targetRole"],
        extra={"request_id": result["id"]},
    )
    return result


@approvals_router.get(
    "/metrics",
    response_model=ApprovalQueueMetricsOut,
    dependencies=[Depends(require_permission("artifact:view"))],
)
async def queue_metrics(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> ApprovalQueueMetricsOut:
    gates = await _pending_gates(db, request)
    now = datetime.now(tz=timezone.utc)
    oldest = 0
    if gates:
        oldest_at = min(datetime.fromisoformat(g.requestedAt) for g in gates)
        oldest = max(0, int((now - oldest_at).total_seconds() // 60))
    return ApprovalQueueMetricsOut(
        approvals=sum(1 for g in gates if g.type == "approval"),
        clarifications=0,
        oldestMinutes=oldest,
        generatedAt=now.isoformat(),
    )
