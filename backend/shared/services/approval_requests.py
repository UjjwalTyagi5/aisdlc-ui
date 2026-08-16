"""Approval-request service — creation, decision, and the self-approval rule.

THE CHECK LIVES HERE, NOT IN THE CONTROLLER
-------------------------------------------
`decide()` compares the caller against `initiator_id` before anything else. It is in
the service layer deliberately: a controller-level check protects exactly one HTTP
route, and this operation is reachable from more than one — a second route, a queue
consumer draining decisions, a bulk action, an internal caller. Each of those would
need its own copy, and the copy that gets forgotten is the hole.

Put another way: the rule belongs to the operation, not to one way of invoking it.

WHY 400 AND NOT 403
-------------------
403 says "you may not do this kind of thing". Self-approval is not that — the caller
may well hold the exact permission required, and would be allowed to decide this same
request had someone else raised it. What is wrong is the combination, which is a bad
request, not insufficient authority. The error code and the message both say so.

NO FALLBACK APPROVER
--------------------
`target_role` records who SHOULD decide a request; nothing substitutes a different
approver when nobody holds it. A request that cannot be actioned stays pending and
visible, which is a state someone can see and fix — by granting the role, or by
deciding it as whoever legitimately can.

The alternative was rejected deliberately: routing to a Project Admin when the
specialist is missing means the approval is recorded as satisfied while the person
whose judgement the gate existed to obtain never saw it. An approval that quietly
changes who gave it is worse than one that visibly waits.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

VALID_DECISIONS = ("approved", "rejected")


class ApprovalError(Exception):
    """Base for approval-service failures, carrying a machine-readable code."""

    code = "APPROVAL_ERROR"
    http_status = 400

    def __init__(self, message: str, *, code: str | None = None, http_status: int | None = None):
        super().__init__(message)
        if code:
            self.code = code
        if http_status:
            self.http_status = http_status


class SelfApprovalBlocked(ApprovalError):
    code = "SELF_APPROVAL_BLOCKED"
    http_status = 400


class ApprovalNotFound(ApprovalError):
    code = "NOT_FOUND"
    http_status = 404


class ApprovalAlreadyDecided(ApprovalError):
    code = "ALREADY_DECIDED"
    http_status = 409


async def create_request(
    session: AsyncSession,
    *,
    tenant_id: str,
    initiator_id: str,
    subject_kind: str,
    subject_id: Optional[str],
    title: str,
    target_role: str,
    scope_kind: str,
    scope_id: str,
    detail: Optional[str] = None,
    request_type: str = "standard",
) -> dict[str, Any]:
    """Raise an approval request. Returns the stored row.

    `initiator_id` is required and not defaulted from anywhere: a request whose
    initiator is unknown cannot be checked against its approver, so allowing one would
    quietly create rows exempt from the rule below.
    """
    if not initiator_id:
        raise ApprovalError("initiator_id is required", code="INITIATOR_REQUIRED", http_status=422)
    if request_type not in ("standard", "specialist_required"):
        raise ApprovalError(f"unknown request_type {request_type!r}", code="INVALID_TYPE", http_status=422)

    request_id = str(_uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO approval_requests "
            "  (id, tenant_id, initiator_id, subject_kind, subject_id, title, detail, "
            "   target_role, scope_kind, scope_id, request_type) "
            "VALUES (:id, :t, :init, :sk, :sid, :title, :detail, :role, :scope_kind, "
            "        CAST(:scope_id AS uuid), :rtype)"
        ),
        {
            "id": request_id, "t": tenant_id, "init": initiator_id,
            "sk": subject_kind, "sid": subject_id, "title": title, "detail": detail,
            "role": target_role, "scope_kind": scope_kind, "scope_id": scope_id,
            "rtype": request_type,
        },
    )
    logger.info(
        "approval request raised: id=%s by=%s target_role=%s scope=%s:%s type=%s",
        request_id, initiator_id, target_role, scope_kind, scope_id, request_type,
    )
    return await get_request(session, request_id)


async def get_request(session: AsyncSession, request_id: str) -> dict[str, Any]:
    try:
        rid = str(_uuid.UUID(str(request_id)))
    except (ValueError, AttributeError):
        raise ApprovalNotFound("approval request not found")

    row = (await session.execute(
        text(
            "SELECT id, tenant_id, initiator_id, subject_kind, subject_id, title, detail, "
            "       target_role, scope_kind, scope_id, request_type, status, "
            "       decided_by, decided_at, decision_reason, created_at "
            "FROM approval_requests WHERE id = CAST(:i AS uuid)"
        ),
        {"i": rid},
    )).first()
    if row is None:
        # RLS also lands here for another tenant's request, which is the intended
        # answer: not found and not permitted are indistinguishable from outside.
        raise ApprovalNotFound("approval request not found")
    return {
        "id": str(row.id),
        "tenantId": str(row.tenant_id),
        "initiatorId": row.initiator_id,
        "subjectKind": row.subject_kind,
        "subjectId": row.subject_id,
        "title": row.title,
        "detail": row.detail,
        "targetRole": row.target_role,
        "scopeKind": row.scope_kind,
        "scopeId": str(row.scope_id),
        "requestType": row.request_type,
        "status": row.status,
        "decidedBy": row.decided_by,
        "decidedAt": row.decided_at.isoformat() if row.decided_at else None,
        "decisionReason": row.decision_reason,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


async def decide(
    session: AsyncSession,
    *,
    request_id: str,
    decider_id: str,
    decision: str,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    """Approve or reject a request. Refuses if the decider raised it.

    Order matters: identity, then existence, then the self-approval rule, then state.
    The self-approval check runs BEFORE the already-decided check so that a second
    attempt by the initiator is still reported as self-approval — the more specific
    and more actionable of the two answers.
    """
    if decision not in VALID_DECISIONS:
        raise ApprovalError(
            f"decision must be one of {VALID_DECISIONS}", code="INVALID_DECISION", http_status=422
        )
    if not decider_id:
        raise ApprovalError("decider is unknown", code="NO_DECIDER", http_status=401)

    request = await get_request(session, request_id)

    # ── the rule ─────────────────────────────────────────────────────────────
    if request["initiatorId"] == decider_id:
        logger.warning(
            "self-approval blocked: request=%s user=%s", request["id"], decider_id
        )
        raise SelfApprovalBlocked(
            "You cannot decide a request you raised yourself. "
            "It must be actioned by someone else holding the required role."
        )

    if request["status"] != "pending":
        raise ApprovalAlreadyDecided(
            f"this request was already {request['status']}"
        )

    now = datetime.now(tz=timezone.utc)
    # Guarded on status in the UPDATE as well as checked above: two approvers acting at
    # the same instant both pass the read, and only one should win. The row count tells
    # us which one did.
    result = await session.execute(
        text(
            "UPDATE approval_requests "
            "   SET status = :s, decided_by = :by, decided_at = :at, decision_reason = :why "
            " WHERE id = CAST(:i AS uuid) AND status = 'pending'"
        ),
        {"s": decision, "by": decider_id, "at": now, "why": reason, "i": request["id"]},
    )
    if not result.rowcount:
        raise ApprovalAlreadyDecided("this request was decided by someone else first")

    logger.info(
        "approval request %s: id=%s by=%s (raised by %s)",
        decision, request["id"], decider_id, request["initiatorId"],
    )
    return await get_request(session, request["id"])


async def list_requests(
    session: AsyncSession, *, status: Optional[str] = None
) -> list[dict[str, Any]]:
    """Requests in this tenant, oldest first — SLA pressure rises to the top."""
    sql = (
        "SELECT id FROM approval_requests"
        + (" WHERE status = :s" if status else "")
        + " ORDER BY created_at ASC"
    )
    rows = (await session.execute(text(sql), {"s": status} if status else {})).fetchall()
    return [await get_request(session, str(r.id)) for r in rows]
