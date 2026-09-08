"""Cross-run pending-gate queue — GET /approvals, GET /approvals/metrics.

A *gate* is a run paused for a human. There is no approvals table: the run row
itself carries the state (`gate_pending` + `current_stage`), so a gate is derived,
not stored, and cannot drift from the run it belongs to.

A PENDING DOCUMENT IS THE SECOND SOURCE, and it was missing entirely. Uploading a
document to a stage raises a real approval — `artifacts.approval_status = 'pending'`,
decided by `POST /artifacts/{id}/approve` — but nothing listed it here, so it appeared
only in the Documents panel of the one stage page it belonged to. Somebody whose job is
to approve things went to Requests & Approvals, the page that exists to answer "what is
waiting on me", and was told nothing was.

DERIVED FOR THE SAME REASON, and it is what makes the first-approver rule work. A
document has two equal approvers: the stage's owning role and the project's
administrator, either of whom may decide it (`_artifact_for_decision`). Because this
reads `approval_status` rather than storing a task per approver, the moment either one
approves, the row leaves BOTH queues on the next fetch — there is no second copy to
withdraw, and no way for one to go stale.

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
    # OPTIONAL SINCE DOCUMENTS JOINED THE QUEUE. A run gate always has both; an
    # uploaded document has neither by nature — `artifacts.run_id` is nullable (a
    # document outlives the run that produced it, and an uploaded one never had one)
    # and `stage` is NULL for a project-wide document, which belongs to no agent.
    # Run gates still always populate them.
    runId: Optional[str] = None
    projectId: str
    projectName: str
    phase: Optional[str] = None
    agentType: Optional[str] = None
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


async def _pending_documents(db: AsyncSession, request: Request) -> list[ApprovalGateOut]:
    """Documents waiting on a person, as queue rows.

    Scoped by the same `allowed_workspace_ids` the run gates use, so this cannot widen
    what a viewer sees; `requiredPermission` then decides whose queue it lands in, and
    the endpoint that actually takes the decision re-checks everything anyway.

    `story` rows are excluded. They are projections of a run's requirements payload with
    synthesised ids and no blob — there is nothing to approve and no approver, so a row
    for one would sit in the queue forever with no way to clear it.
    """
    allowed = await allowed_workspace_ids(db, request)
    scoped = allowed is not None
    clause = " AND p.workspace_id = ANY(CAST(:ws AS uuid[]))" if scoped else ""

    rows = (await db.execute(
        text(
            "SELECT a.id, a.stage, a.run_id, a.blob_path, a.artifact_type, "
            "       a.uploaded_by, a.created_at, "
            "       p.id AS project_id, p.display_name AS project_name "
            "FROM artifacts a JOIN projects p ON p.id = a.project_id "
            "WHERE a.approval_status = 'pending' AND a.artifact_type <> 'story'"
            + clause +
            " ORDER BY a.created_at ASC"
        ),
        {"ws": allowed or []},
    )).fetchall()

    out: list[ApprovalGateOut] = []
    for r in rows:
        stage = r.stage
        ui_phase = _PHASE_TO_UI.get(stage, stage) if stage else None
        # THE TWO APPROVERS, expressed as the one permission the queue filters on.
        # A stage document names its owning role's permission; a project-wide one has
        # no owning role, so it names `approve`, which is what project administration
        # carries. The frontend additionally lets a Project Admin see every gate on
        # their project, which is the other half of the same rule.
        permission = _PHASE_PERMISSION.get(stage or "", "approve")
        name = (r.blob_path or "").rsplit("/", 1)[-1] or "document"
        scope_label = f"{ui_phase.replace('_', ' ')} " if ui_phase else "project-wide "
        out.append(ApprovalGateOut(
            # Namespaced so it cannot collide with a run gate's `{run}:{phase}`.
            id=f"artifact:{r.id}",
            type="document",
            runId=str(r.run_id) if r.run_id else None,
            projectId=str(r.project_id),
            projectName=r.project_name,
            phase=ui_phase,
            agentType=ui_phase,
            requiredPermission=permission,
            capabilityClass="consequential",
            mandatory=False,
            title=f"{name} awaiting approval",
            summary=(
                f"{r.project_name} — a {scope_label}document is waiting for an owner "
                "to accept it into the project's record."
            ),
            # The person who put it there, unlike a run gate, which is always the agent
            # that finished the stage. Naming them is what lets an approver tell an
            # expected upload from one they should ask about.
            requestedBy=r.uploaded_by or "agent",
            requestedAt=r.created_at.astimezone(timezone.utc).isoformat(),
            artifact=GateArtifactRef(id=str(r.id), title=name, type=r.artifact_type or "document"),
        ))
    return out


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
    # Oldest first ACROSS BOTH SOURCES, so a document waiting three days is not sorted
    # below a gate raised this morning purely because it came from a different table.
    merged = gates + await _pending_documents(db, request)
    merged.sort(key=lambda g: g.requestedAt)
    return merged


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
    # BOTH SOURCES, or this tile disagrees with the list under it. Counting only run
    # gates would report "0 approvals pending" on a project whose only pending item is
    # a document — while the queue itself showed it — and a summary that contradicts
    # the thing it summarises is worse than no summary.
    waiting = await _pending_gates(db, request) + await _pending_documents(db, request)
    now = datetime.now(tz=timezone.utc)
    oldest = 0
    if waiting:
        oldest_at = min(datetime.fromisoformat(g.requestedAt) for g in waiting)
        oldest = max(0, int((now - oldest_at).total_seconds() // 60))
    return ApprovalQueueMetricsOut(
        # `document` counts as an approval: it is one thing waiting on a person, which
        # is what the tile means. The type distinction matters to the row that renders
        # it, not to the count.
        approvals=sum(1 for g in waiting if g.type in ("approval", "document")),
        clarifications=0,
        oldestMinutes=oldest,
        generatedAt=now.isoformat(),
    )
