"""Raising a draft document for approval — one implementation for every caller.

TWO CALLERS, ONE ACT. The Documents panel's "Raise for approval" button
(POST /artifacts/{id}/submit) and the agent's `raise_document_for_approval` tool both
put a draft forward. They must agree on what that means — which statuses may move, what
is recorded — so the rule lives here and each caller only translates the outcome.

`draft -> pending`. Re-raising something already pending is a no-op, never an error. An
approved or rejected document is refused: raising must never quietly reopen a decision
somebody already took. See `shared/routers/artifacts.py::submit_artifact` for why this
is `run:create` and not `approve`.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def may_raise_for_approval(
    db: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    user_id: str,
    stage: Optional[str],
    permissions: Optional[list] = None,
) -> bool:
    """Who may put a document forward for approval.

    Whoever holds `run:create` on the project — asking is producing, not accepting — OR
    whoever can USE the agent the document belongs to: its owning role, or a person given
    it as an extra agent.

    THE SECOND HALF IS A PRODUCT DECISION (2026-09-16). QA could approve Testing documents
    but not send one for approval, and a Security Engineer granted Code Review could run a
    review but not raise its report — `run:create` is not in those roles. Using an agent
    now carries putting its output forward. The owner still decides afterwards.

    A project-wide document (no stage) belongs to no agent, so only `run:create` raises it.
    `permissions` is the caller's already-resolved list when there is one (the REST route);
    otherwise `run:create` is resolved for the project (the chat tool). Project membership
    is the caller's check — both callers make it before asking this.
    """
    if permissions is not None:
        from shared.authz.permissions import has_permission  # noqa: PLC0415

        if has_permission(permissions, "run:create"):
            return True
    else:
        from shared.authz.can_perform import can_perform  # noqa: PLC0415

        if await can_perform(
            db, user_id=str(user_id), permission="run:create", tenant_id=str(tenant_id),
            resource_kind="project", resource_id=str(project_id),
        ):
            return True
    if not stage:
        return False
    from shared.authz.agent_access import check_agent_access  # noqa: PLC0415
    from shared.authz.effective_role import platform_role_for  # noqa: PLC0415

    role = await platform_role_for(db, user_id=str(user_id), permissions=[])
    return await check_agent_access(
        db, tenant_id=str(tenant_id), project_id=str(project_id), role=role,
        user_id=str(user_id), agent_id=stage,
    )


class AlreadyDecided(Exception):
    """The document was approved or rejected; it cannot be raised again."""

    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


async def submit_for_approval(
    db: AsyncSession, artifact, *, tenant_id: str, actor_id: Optional[str]
) -> bool:
    """Move `artifact` from draft to pending and audit it.

    Returns True when it moved, False when it was already pending. Raises
    `AlreadyDecided` for an approved or rejected document. The caller owns the session
    (commit/rollback) and has already checked the caller may do this.
    """
    from shared.models.orm import AuditEvent  # noqa: PLC0415
    from shared.authz.audit import capture_names  # noqa: PLC0415

    status = artifact.approval_status or "draft"
    if status in ("approved", "rejected"):
        raise AlreadyDecided(status)
    if status == "pending":
        return False

    artifact.approval_status = "pending"
    db.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor_id=actor_id,
            event_type="artifact_submit",
            resource_type="artifact",
            resource_id=str(artifact.id),
            payload=await capture_names(
                db,
                {
                    "project_id": str(artifact.project_id),
                    "stage": artifact.stage,
                    # The status it is LEAVING, read before the assignment overwrote
                    # it — PRD §34.9's before/after.
                    "before": status,
                    "after": "pending",
                    "artifact_type": artifact.artifact_type,
                },
                actor_id=actor_id,
            ),
        )
    )
    await db.flush()
    await db.refresh(artifact)
    await notify_submitted(db, artifact, tenant_id=tenant_id, actor_id=actor_id)
    return True


def _agent_href(project_id, stage: Optional[str]) -> str:
    """The screen the document lives on: its agent's page, or the project for a
    project-wide document. Backend stage `code_review` is the route `code-review`."""
    base = f"/projects/{project_id}"
    return f"{base}/{stage.replace('_', '-')}" if stage else base


async def _describe(db: AsyncSession, artifact, tenant_id: str) -> tuple[str, str, str]:
    """(document name, project name, stage label) for a notification's wording."""
    name = (artifact.blob_path or "").rsplit("/", 1)[-1] or "document"
    project_name = (await db.execute(
        text("SELECT display_name FROM projects WHERE id = CAST(:p AS uuid)"),
        {"p": str(artifact.project_id)},
    )).scalar() or "the project"
    stage = (artifact.stage or "").replace("_", " ").strip() or "project-wide"
    return name, project_name, stage


async def _label(db: AsyncSession, tenant_id: str, user_id: Optional[str]) -> str:
    from shared.services.actor_labels import actor_labels, relabel  # noqa: PLC0415

    if not user_id:
        return "Someone"
    labels = await actor_labels(db, tenant_id, [user_id])
    return relabel(user_id, labels) or "Someone"


async def notify_submitted(
    db: AsyncSession, artifact, *, tenant_id: str, actor_id: Optional[str]
) -> None:
    """Tell the project's administrators a document is waiting for them.

    The Requests & Approvals queue already lists it; this puts it in the bell too.
    Best-effort: `notifications.emit` never raises, and this must never fail the
    submission it announces.
    """
    from shared.services import notifications  # noqa: PLC0415

    try:
        name, project_name, stage = await _describe(db, artifact, tenant_id)
        who = await _label(db, tenant_id, actor_id)
        await notifications.emit(
            db,
            tenant_id=str(tenant_id),
            kind="document_approval_required",
            title=f"{name} is awaiting your approval",
            body=f"{who} sent the {stage} document for approval on {project_name}.",
            href=_agent_href(artifact.project_id, artifact.stage),
            recipient_role="project_admin",
            recipient_scope_kind="project",
            recipient_scope_id=str(artifact.project_id),
            project_id=str(artifact.project_id),
            run_id=str(artifact.run_id) if artifact.run_id else None,
        )
    except Exception:  # noqa: BLE001
        logger.warning("approval-required notice failed for %s", artifact.id, exc_info=True)


async def notify_decided(
    db: AsyncSession, artifact, *, tenant_id: str, decision: str, decided_by: Optional[str],
) -> None:
    """Tell whoever put the document forward that it was approved or rejected.

    The submitter is read from the audit trail (`artifact_submit`), because nothing on
    the artifact records who pressed the button; `uploaded_by` is the fallback for a
    document that predates that event. Nobody is told about their own decision.
    """
    from shared.models.orm import AuditEvent  # noqa: PLC0415
    from shared.services import notifications  # noqa: PLC0415

    try:
        submitter = (await db.execute(
            select(AuditEvent.actor_id)
            .where(
                AuditEvent.event_type == "artifact_submit",
                AuditEvent.resource_id == str(artifact.id),
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(1)
        )).scalar() or artifact.uploaded_by
        if not submitter or str(submitter) == str(decided_by or ""):
            return

        name, project_name, stage = await _describe(db, artifact, tenant_id)
        who = await _label(db, tenant_id, decided_by)
        approved = decision == "approved"
        body = f"{who} {'approved' if approved else 'rejected'} the {stage} document on {project_name}."
        if not approved and getattr(artifact, "rejection_reason", None):
            body += f" Reason: {artifact.rejection_reason}"
        await notifications.emit(
            db,
            tenant_id=str(tenant_id),
            kind="document_approved" if approved else "document_rejected",
            title=f"{name} was {'approved' if approved else 'rejected'}",
            body=body,
            href=_agent_href(artifact.project_id, artifact.stage),
            recipient_user_id=str(submitter),
            project_id=str(artifact.project_id),
            run_id=str(artifact.run_id) if artifact.run_id else None,
        )
    except Exception:  # noqa: BLE001
        logger.warning("decision notice failed for %s", artifact.id, exc_info=True)
