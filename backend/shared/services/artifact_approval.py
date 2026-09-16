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

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession


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
    return True
