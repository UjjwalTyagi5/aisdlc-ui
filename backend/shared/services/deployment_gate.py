"""The approval gate for anything that reaches an environment.

Deployment agent phase 1.

THE RULE. Generating deployment files is free. Creating a pipeline, starting a run, or
applying to a cluster each change something outside the platform, and each one goes
through here: a `pending` row, a named human, then execution.

FOUR THINGS THIS REFUSES, each of which is a way a deploy happens that nobody agreed to:

  self-approval        The person who asked cannot be the person who approves. Matches
                       the run-gate rule in tests/test_gate_self_approval.py.
  re-execution         `claim_for_execution` is a conditional UPDATE, so one approval
                       fires exactly once even if two workers race for it. Without
                       that, an approval is a standing licence to deploy.
  a changed request    What was approved is what runs. `request` is frozen once the
                       decision is taken; a mutable request means somebody approves a
                       staging deploy and production goes out.
  a second decision    An approved or rejected deployment is closed. Re-deciding it
                       would let a rejection be quietly overturned.

The database enforces the first, third and fourth of these too (migration 0043). These
functions are not the only line of defence, deliberately: a service-layer mistake here
should still be unable to deploy.

NO COMMITS. Every function leaves the transaction open for the caller, exactly as the
artifact routes do — `get_db_session` sets the RLS tenant transaction-locally and owns
the single commit at request end. Committing here drops the tenant and the next
statement in the same request reads an empty table.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.orm import AuditEvent, Deployment

logger = logging.getLogger(__name__)

#: Actions that must be approved before they happen. Everything else the deployment
#: agent does — reading the repo, generating files, staging them — needs no row here.
GATED_ACTIONS = ("create_pipeline", "run_pipeline", "direct_apply")


class DeploymentGateError(Exception):
    """A deployment decision or execution that must not proceed.

    Carries a `reason` the caller can show a user: "this was already approved" and
    "you cannot approve your own request" need different responses, and a single
    opaque failure would leave the UI guessing.
    """

    def __init__(self, reason: str, code: str = "refused") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


async def request_deployment(
    db: AsyncSession, *, tenant_id: str, project_id: str, action: str,
    target_kind: str, environment: str, request: Dict[str, Any], requested_by: str,
    run_id: Optional[str] = None,
) -> Deployment:
    """Record a deployment somebody wants, in the `pending` state.

    This is the ONLY way a gated action gets started. It performs nothing — the point
    is that asking and doing are separate steps with a human in between.
    """
    if action not in GATED_ACTIONS:
        raise DeploymentGateError(
            f"{action!r} is not a gated deployment action; expected one of "
            f"{', '.join(GATED_ACTIONS)}.",
            code="unknown_action",
        )
    if not requested_by:
        # An unattributed request cannot be checked for self-approval, which would
        # make the self-approval rule silently unenforceable.
        raise DeploymentGateError(
            "A deployment request must name who asked for it.", code="no_requester"
        )
    if not environment:
        raise DeploymentGateError(
            "A deployment request must name its environment — 'somewhere' is not a "
            "thing anyone can approve.",
            code="no_environment",
        )

    dep = Deployment(
        tenant_id=tenant_id, project_id=project_id, run_id=run_id, action=action,
        target_kind=target_kind, environment=environment, request=request,
        requested_by=requested_by, approval_status="pending",
        execution_status="not_started",
    )
    db.add(dep)
    await db.flush()
    db.add(_audit(tenant_id, requested_by, "deployment_request", dep))
    return dep


async def approve_deployment(
    db: AsyncSession, *, deployment_id: str, tenant_id: str, approver: str,
) -> Deployment:
    """Accept a pending deployment. The approver may not be the requester."""
    dep = await _load(db, deployment_id, tenant_id)

    if dep.approval_status == "approved":
        # Idempotent for a double-click, but only for the SAME approver: a second
        # person clicking approve on an already-approved row should not silently
        # rewrite who signed for it.
        if dep.approved_by == approver:
            return dep
        raise DeploymentGateError(
            f"Already approved by {dep.approved_by}.", code="already_approved"
        )
    if dep.approval_status == "rejected":
        raise DeploymentGateError(
            "This deployment was rejected. Raise a new request rather than "
            "overturning the decision in place.",
            code="already_rejected",
        )
    if not approver:
        raise DeploymentGateError("An approval must name its approver.", code="no_approver")
    if approver == dep.requested_by:
        raise DeploymentGateError(
            "You cannot approve your own deployment request. Someone else with "
            "artifact:approve_deployment has to take this decision.",
            code="self_approval",
        )

    dep.approval_status = "approved"
    dep.approved_by = approver
    dep.approved_at = datetime.now(timezone.utc)
    db.add(_audit(tenant_id, approver, "deployment_approve", dep))
    return dep


async def reject_deployment(
    db: AsyncSession, *, deployment_id: str, tenant_id: str, approver: str,
    reason: str = "",
) -> Deployment:
    """Refuse a pending deployment.

    Rejecting your own request is allowed — withdrawing something you asked for is not
    the risk the self-approval rule guards against.
    """
    dep = await _load(db, deployment_id, tenant_id)

    if dep.approval_status == "rejected":
        return dep
    if dep.approval_status == "approved":
        if dep.executed_at is not None:
            raise DeploymentGateError(
                "This deployment has already run. Rejecting it now would not undo it — "
                "roll back instead.",
                code="already_executed",
            )
        raise DeploymentGateError(
            f"Already approved by {dep.approved_by}.", code="already_approved"
        )

    dep.approval_status = "rejected"
    dep.approved_by = approver or None
    dep.approved_at = datetime.now(timezone.utc)
    dep.rejection_reason = reason or None
    db.add(_audit(tenant_id, approver, "deployment_reject", dep, reason=reason))
    return dep


async def claim_for_execution(
    db: AsyncSession, *, deployment_id: str, tenant_id: str,
) -> Deployment:
    """Take exclusive ownership of an approved deployment, or refuse.

    ONE APPROVAL, ONE DEPLOYMENT. This is a single conditional UPDATE rather than a
    read-then-write: two workers reaching an approved row at the same moment would both
    pass a Python-level check and both deploy. The WHERE clause is the lock — whoever
    the database lets through gets the row, and the loser is told the truth.
    """
    result = await db.execute(
        update(Deployment)
        .where(
            Deployment.id == deployment_id,
            Deployment.tenant_id == tenant_id,
            Deployment.approval_status == "approved",
            Deployment.executed_at.is_(None),
        )
        .values(executed_at=datetime.now(timezone.utc), execution_status="running")
        .returning(Deployment.id)
    )
    if result.scalar_one_or_none() is None:
        # Nothing was claimed. Say WHICH of the reasons it was — "could not start" on
        # its own sends someone looking for an outage when the answer is that nobody
        # approved it.
        dep = await _load(db, deployment_id, tenant_id, missing_ok=True)
        if dep is None:
            raise DeploymentGateError("No such deployment.", code="not_found")
        if dep.approval_status == "pending":
            raise DeploymentGateError(
                "This deployment has not been approved yet.", code="not_approved"
            )
        if dep.approval_status == "rejected":
            raise DeploymentGateError("This deployment was rejected.", code="rejected")
        raise DeploymentGateError(
            f"This deployment already ran at {dep.executed_at:%Y-%m-%d %H:%M UTC}. "
            "An approval covers one deployment; raise a new request to deploy again.",
            code="already_executed",
        )

    dep = await _load(db, deployment_id, tenant_id)
    db.add(_audit(tenant_id, dep.approved_by or "system", "deployment_execute", dep))
    return dep


async def record_outcome(
    db: AsyncSession, *, deployment_id: str, tenant_id: str, status: str,
    external_id: str = "", external_url: str = "",
    outcome: Optional[Dict[str, Any]] = None,
) -> Deployment:
    """Record what actually happened.

    `execution_status` is kept apart from `approval_status` on purpose: an approved
    deployment that failed is not a rejected one, and collapsing the two loses the fact
    that a human said yes.
    """
    valid = ("running", "succeeded", "failed", "canceled", "error")
    if status not in valid:
        raise DeploymentGateError(
            f"{status!r} is not a deployment outcome; expected one of {', '.join(valid)}.",
            code="unknown_status",
        )
    dep = await _load(db, deployment_id, tenant_id)
    dep.execution_status = status
    if external_id:
        dep.external_id = str(external_id)
    if external_url:
        dep.external_url = external_url
    if outcome is not None:
        dep.outcome = outcome
    return dep


async def list_deployments(
    db: AsyncSession, *, tenant_id: str, project_id: str, pending_only: bool = False,
    limit: int = 50,
) -> list[Deployment]:
    stmt = (
        select(Deployment)
        .where(Deployment.tenant_id == tenant_id, Deployment.project_id == project_id)
        .order_by(Deployment.requested_at.desc())
        .limit(limit)
    )
    if pending_only:
        stmt = stmt.where(Deployment.approval_status == "pending")
    return list((await db.execute(stmt)).scalars().all())


# ── internals ────────────────────────────────────────────────────────────────


async def _load(
    db: AsyncSession, deployment_id: str, tenant_id: str, *, missing_ok: bool = False,
) -> Optional[Deployment]:
    """Fetch one deployment, scoped to the tenant.

    The id is validated before it reaches the query: a synthesised, non-UUID id used to
    raise a database error rather than a clean not-found, which surfaced as a 500 on
    what is really a 404.
    """
    import uuid as _uuid

    try:
        _uuid.UUID(str(deployment_id))
    except (ValueError, AttributeError, TypeError):
        if missing_ok:
            return None
        raise DeploymentGateError("No such deployment.", code="not_found") from None

    dep = (await db.execute(
        select(Deployment).where(
            Deployment.id == deployment_id, Deployment.tenant_id == tenant_id
        )
    )).scalar_one_or_none()
    if dep is None and not missing_ok:
        raise DeploymentGateError("No such deployment.", code="not_found")
    return dep


def _audit(
    tenant_id: str, actor: str, event_type: str, dep: Deployment, **extra: Any
) -> AuditEvent:
    """The record that survives whatever happens next.

    Carries the request itself, not just its id: this is the evidence of what was
    approved, and a reader should not have to trust that the row was never edited.
    """
    return AuditEvent(
        tenant_id=tenant_id,
        actor_id=actor or None,
        event_type=event_type,
        resource_type="deployment",
        resource_id=str(dep.id),
        payload={
            "project_id": str(dep.project_id),
            "action": dep.action,
            "target_kind": dep.target_kind,
            "environment": dep.environment,
            "request": dep.request,
            "requested_by": dep.requested_by,
            "approval_status": dep.approval_status,
            **{k: v for k, v in extra.items() if v},
        },
    )
