"""Persist chat-generated files (docx / pptx / png / svg) as project Artifact rows.

Chat file tools (markdowntodoc, generate_ppt, generate_diagram, save_architecture …)
broadcast a live `file_generated` WS event but never wrote an Artifact row — so a doc /
PPT / image produced in an agent chat never showed up in the project artifacts panel,
which reads Artifact ⨝ Run. This bridges that gap: using the chat's tenant/project/run
context (config.ws_helper), it reuses the pipeline run when present, else mints ONE
lightweight per-project "chat" Run for the stage, then inserts an Artifact row
(blob_url / blob_path / content_type / size_bytes) and publishes artifact_ready so the
panel live-refreshes. Purely additive — the existing WS download is unchanged.

Best-effort: every failure is swallowed so file generation is never broken by it.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import select

from config.ws_helper import get_project_id, get_run_id, get_tenant_id
from shared.db import get_db_session_for_tenant
from shared.models.orm import Artifact, Run

logger = logging.getLogger(__name__)

_CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _artifact_type_for(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".png", ".svg", ".jpg", ".jpeg"):
        return "diagram"
    if ext == ".pptx":
        return "presentation"
    return "document"


async def _get_or_create_chat_run(session, tenant_id: str, project_id: str, stage: str) -> str | None:
    # 1. Reuse the pipeline run if one is in chat context and belongs to this tenant.
    ctx_run = get_run_id()
    if ctx_run:
        row = (await session.execute(
            select(Run.id).where(Run.id == ctx_run, Run.tenant_id == tenant_id)
        )).first()
        if row:
            return str(row[0])
    # 2. Reuse an existing chat run for this project+stage — one per project, not per file.
    existing = (await session.execute(
        select(Run.id)
        .where(Run.project_id == project_id, Run.tenant_id == tenant_id,
               Run.stage == stage, Run.trigger == "chat")
        .order_by(Run.created_at.desc())
    )).first()
    if existing:
        return str(existing[0])
    # 3. Mint a lightweight chat run so file artifacts have a home.
    run = Run(project_id=project_id, tenant_id=tenant_id, stage=stage, status="completed", trigger="chat")
    session.add(run)
    await session.flush()
    return str(run.id)


async def register_generated_file(filename: str, file_path: str, url: str, *, stage: str) -> None:
    """Persist a chat-generated file as an Artifact row (+ notify). Never raises."""
    try:
        tenant_id = get_tenant_id()
        project_id = get_project_id()
        if not tenant_id or not project_id:
            logger.debug("register_generated_file: no tenant/project in context — skip persist (%s)", filename)
            return

        size = os.path.getsize(file_path) if file_path and os.path.exists(file_path) else None
        content_type = _CONTENT_TYPES.get(os.path.splitext(filename)[1].lower())
        artifact_type = _artifact_type_for(filename)

        async with get_db_session_for_tenant(tenant_id) as session:
            run_id = await _get_or_create_chat_run(session, tenant_id, project_id, stage)
            if not run_id:
                return
            session.add(Artifact(
                run_id=run_id, tenant_id=tenant_id, artifact_type=artifact_type,
                blob_url=url, blob_path=file_path, content_type=content_type, size_bytes=size,
            ))

        try:
            from shared.services.artifact_service import publish_artifact_ready  # noqa: PLC0415
            await publish_artifact_ready(run_id, artifact_type, tenant_id=tenant_id)
        except Exception:
            logger.debug("register_generated_file: artifact_ready notify failed", exc_info=True)

        logger.info("register_generated_file: persisted %s (%s) for run %s", filename, artifact_type, run_id)
    except Exception:
        logger.warning("register_generated_file: failed to persist %s", filename, exc_info=True)
