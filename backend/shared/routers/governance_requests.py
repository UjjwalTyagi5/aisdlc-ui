"""Governance requests — the Requests & Approvals queue.

DISTINCT FROM `/approvals`, which is the other lane of PRD §33.2. That one derives
gates from `runs.gate_pending`: an agent paused for a human, with no initiator. This
one is raised BY a person and climbs tiers until one can grant it. They share a screen
in the UI because both are "things waiting on someone"; they share no routing, and
merging them would breach the guardrail that approvals never route to a governance tier.

THIN BY DESIGN. Every rule — self-approval, current-approver, escalation ceiling,
apply-on-approve — is in `shared/services/governance_requests.py`, because these
operations are reachable from more than one route and a rule enforced at the controller
protects exactly one of them.

THE PERMISSION FLOOR IS `artifact:view`, WHICH IS NOT A MISTAKE. Every signed-in person
may raise a request and see their own; that is the point of the lane. Who may DECIDE one
is a question about ROLE, not permission, and is answered per request against
`current_approver_role` — a Business Unit Admin and a Project Admin both hold
`member:manage` and sit on different rungs, so no permission string can express it.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.dependency import require_permission
from shared.authz.effective_role import actor_display_name, effective_platform_role
from shared.authz.read_scope import allowed_workspace_ids
from shared.db import get_db_session
from shared.governance import routing
from shared.services import governance_requests as service
from shared.services.governance_requests import GovernanceError

logger = logging.getLogger(__name__)

governance_router = APIRouter(
    prefix="/governance-approvals",
    dependencies=[Depends(require_permission("artifact:view"))],
)


class AttachmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    sizeBytes: int = Field(ge=0)
    contentType: str = Field(max_length=255)


class RequestCreateIn(BaseModel):
    """What a person may send when raising a request.

    Conspicuously ABSENT: the requester's identity, their role, and the approver.
    All three are derived server-side from the session — a client that could name
    its own role would pick the one whose chain is shortest, and one that could
    name its own approver would not need a chain at all.

    `phase` IS allowed, and the distinction is why: it is content, not routing.
    The requester names the agent they want; the platform derives who owns it.
    """

    type: str
    title: str = Field(min_length=4, max_length=160)
    description: str = Field(min_length=10, max_length=4000)
    priority: str = "normal"
    workspaceId: str = Field(min_length=1)
    projectId: Optional[str] = None
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=10)
    phase: Optional[str] = None
    # WHAT is being asked for, when the type needs a specific one — a
    # connector kind, an MCP server row id, or a model provider id. Content,
    # not routing, exactly like `phase` above: the requester names the thing,
    # the platform still derives who decides it.
    targetId: Optional[str] = Field(default=None, max_length=255)
    # connector_access's access level (read/write/read_write) — required by
    # _apply_connector_access's existing payload.get("access") read (see that
    # function in effects.py). Only meaningful alongside targetId for this
    # one type; unused by mcp_server (which has no level, per migration 0024
    # — see _apply_connector_access's own "no level on the row" comment).
    accessLevel: Optional[str] = Field(default=None, max_length=16)
    # model_credential's target: a project needs BOTH a provider and a model
    # id, not one opaque id.
    providerModel: Optional[dict[str, str]] = None
    # user_onboarding's target: who is being asked about.
    onboardEmail: Optional[str] = Field(default=None, max_length=320)


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    reason: Optional[str] = Field(default=None, max_length=2000)


class ReasonIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=2000)


def _http(exc: GovernanceError) -> HTTPException:
    """Map a service error to its HTTP shape, preserving the machine-readable code.

    The code is what a client branches on; the message is for the person reading
    it. Returning only a message would force clients to match on prose.
    """
    return HTTPException(
        status_code=exc.http_status, detail={"code": exc.code, "message": str(exc)}
    )


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        # Defense in depth: the JWT middleware sets this for every protected route.
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


def _user_id(request: Request) -> str:
    return getattr(request.state, "user_id", "") or ""


async def _actor(db: AsyncSession, request: Request) -> tuple[str, str, Optional[str]]:
    """(id, display name, role) for the caller — the three things every rule needs.

    Resolved together because they are always needed together, and because two of
    the three cost a query: fetching them at each call site is how one route ends
    up recording a raw user id in a timeline where the others record a name.
    """
    return (
        _user_id(request),
        await actor_display_name(db, request),
        await effective_platform_role(db, request),
    )


@governance_router.get("")
async def list_requests(
    request: Request,
    workspaceId: Optional[str] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """The queue, scoped to what the caller may read plus whatever they raised."""
    _tenant_id(request)
    return await service.list_requests(
        db,
        viewer_id=_user_id(request),
        allowed_workspace_ids=await allowed_workspace_ids(db, request),
        workspace_id=workspaceId,
        status=status,
    )


@governance_router.post("", status_code=201)
async def create_request(
    request: Request,
    body: RequestCreateIn,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    tenant_id = _tenant_id(request)
    actor_id, actor_name, actor_role = await _actor(db, request)
    try:
        return await service.create_request(
            db,
            tenant_id=tenant_id,
            initiator_id=actor_id,
            initiator_name=actor_name,
            initiator_role=actor_role,
            request_type=body.type,
            title=body.title,
            description=body.description,
            workspace_id=body.workspaceId,
            project_id=body.projectId,
            priority=body.priority,
            attachments=[a.model_dump() for a in body.attachments],
            phase=body.phase,
            target_id=body.targetId,
            access_level=body.accessLevel,
            provider_model=body.providerModel,
            onboard_email=body.onboardEmail,
        )
    except GovernanceError as exc:
        raise _http(exc)


@governance_router.post(
    "/{request_id}/decide",
    # THE ONLY ROUTE IN THIS FILE THAT GETS THIS GATE, and the omissions are the point:
    #
    #   /cancel   is the INITIATOR's act — withdrawing your own request. An approver who
    #             wants it gone rejects it, which records a decision.
    #   /escalate is open to the initiator too, deliberately: a request that can only be
    #             escalated by the approver ignoring it would never move.
    #
    # Gating either on a decider permission would take those away from the person who
    # raised the request, who is usually a delivery role and holds nothing here.
    #
    # This does NOT replace the routing check in the service — `decider_role` must still
    # equal `currentApproverRole`. Permission answers WHETHER this role decides
    # governance requests at all; the escalation chain answers WHOSE TURN it is. Only
    # the second existed, so authorisation was pure role-string matching and a custom
    # role could be neither granted nor denied a governance decision.
    dependencies=[Depends(require_permission("governance:decide"))],
)
async def decide_request(
    request_id: str,
    request: Request,
    body: DecisionIn,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    _tenant_id(request)
    actor_id, actor_name, actor_role = await _actor(db, request)
    try:
        return await service.decide(
            db,
            request_id=request_id,
            decider_id=actor_id,
            decider_name=actor_name,
            decider_role=actor_role,
            decision=body.decision,
            reason=body.reason,
        )
    except GovernanceError as exc:
        raise _http(exc)


@governance_router.post("/{request_id}/escalate")
async def escalate_request(
    request_id: str,
    request: Request,
    body: ReasonIn,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    _tenant_id(request)
    actor_id, actor_name, actor_role = await _actor(db, request)
    try:
        return await service.escalate(
            db,
            request_id=request_id,
            actor_id=actor_id,
            actor_name=actor_name,
            actor_role=actor_role,
            note=body.reason,
        )
    except GovernanceError as exc:
        raise _http(exc)


@governance_router.post("/{request_id}/cancel")
async def cancel_request(
    request_id: str,
    request: Request,
    body: ReasonIn,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    _tenant_id(request)
    actor_id, actor_name, _ = await _actor(db, request)
    try:
        return await service.cancel(
            db,
            request_id=request_id,
            actor_id=actor_id,
            actor_name=actor_name,
            reason=body.reason,
        )
    except GovernanceError as exc:
        raise _http(exc)


@governance_router.get("/raisable-types")
async def raisable_types(
    request: Request, db: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    """What this caller may ask for, and whether they may ask at all.

    Served rather than left to the client so the picker and the enforcement in
    `create_request` cannot disagree. The frontend has its own copy of the rule for
    the form's live preview; this is the one that decides, and a picker built from
    it can never offer an option the create call will refuse.
    """
    _tenant_id(request)
    role = await effective_platform_role(db, request)
    return {
        "role": role,
        "canRaise": routing.can_raise_request(role),
        "types": list(routing.raisable_types_for(role)),
    }
