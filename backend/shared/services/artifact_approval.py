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
