"""The documents an agent has already filed on a project, as a note for a new conversation.

WHY. A conversation that starts after a document was written knows nothing about it, so
"send the report for approval" was answered by writing the report again — a second draft
beside the first, and the one the person meant still unraised. The note names what is on
file, by the file name the raise tool takes, with its approval state.
"""
from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)

_STATUS = {
    "draft": "draft, not yet raised",
    "pending": "raised, waiting on the approver",
    "approved": "approved",
    "rejected": "rejected",
}


async def filed_documents_note(tenant_id: str, project_id: str, stage: str, *, limit: int = 15) -> str:
    """A prompt note listing `stage`'s documents on the project, newest first — or ""."""
    if not (tenant_id and project_id and stage):
        return ""
    from sqlalchemy import select  # noqa: PLC0415

    from shared.db import get_db_session_for_tenant  # noqa: PLC0415
    from shared.models.orm import Artifact  # noqa: PLC0415

    try:
        async with get_db_session_for_tenant(tenant_id) as db:
            rows = (await db.execute(
                select(Artifact).where(
                    Artifact.project_id == uuid.UUID(str(project_id)), Artifact.stage == stage,
                ).order_by(Artifact.created_at.desc()).limit(limit)
            )).scalars().all()
    except Exception:  # noqa: BLE001 — a note, never a failed turn
        logger.warning("filed-documents lookup failed for project %s stage %s", project_id, stage, exc_info=True)
        return ""
    lines = [
        f"- {(a.blob_path or '').replace(chr(92), '/').rsplit('/', 1)[-1]} "
        f"({_STATUS.get(a.approval_status or 'draft', a.approval_status)}"
        + (f", {a.created_at:%d %b %Y %H:%M} UTC" if getattr(a, "created_at", None) else "") + ")"
        for a in rows if a.blob_path and a.artifact_type != "story"
    ]
    if not lines:
        return ""
    return ("\nDocuments already filed on this project for this stage, newest first:\n"
            + "\n".join(lines)
            + "\nA request to send, raise, publish or explain one of these is about that saved "
            "document — act on it by its file name; do not produce it again.\n")
