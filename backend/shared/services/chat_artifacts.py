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

STORED IN BLOB, NOT ON LOCAL DISK. The row used to carry `blob_url` = a
`/generated/...` static-mount URL and `blob_path` = a local filesystem path. That mount
(`process_api.py`: `app.mount("/generated", StaticFiles(directory=FILES_DIR))`) is in
the JWT middleware's exempt list, so those documents were readable by anyone who knew
the path, across tenants. Files now go through
`shared/services/artifact_store.store_artifact`, which writes them under
`{tenant_id}/{run_id}/{artifact_type}/{filename}` — the prefix IS the tenant boundary —
and the row carries real blob coordinates.

The live in-chat `file_generated` WS event still carries the local `url` so the existing
download keeps working while the static mount is still mounted; retiring that mount is
a separate change.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import select

from config.ws_helper import get_project_id, get_run_id, get_session_id, get_tenant_id
from shared.db import get_db_session_for_tenant
from shared.models.orm import Artifact, Run

logger = logging.getLogger(__name__)

_CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    # jpg/jpeg were already classified as "diagram" by _artifact_type_for but had no
    # content type here, so a browser was handed application/octet-stream and offered
    # a download instead of showing the image. Figma renders jpg on request.
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
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


#: Files generated this session that the user has NOT yet agreed to store.
#: Keyed by session id. In-process and deliberately not persisted: a pending file is
#: a question waiting for an answer in THIS conversation, not durable state.
_PENDING: dict[str, list[dict]] = {}


def pending_for_session(session_id: str) -> list[dict]:
    """What has been generated but not stored. Copy — callers must not mutate."""
    return list(_PENDING.get(session_id or "", []))


def clear_pending(session_id: str) -> None:
    _PENDING.pop(session_id or "", None)


async def register_generated_file(
    filename: str, file_path: str, url: str, *, stage: str, consented: bool | None = None
) -> None:
    """Persist a chat-generated file as an Artifact row (+ notify). Never raises.

    STORING IS NOW THE USER'S CALL. A generated document is downloadable the moment it
    is written — the tool broadcasts a `/generated/...` link — so persisting it into the
    project's artifacts and uploading the bytes to Blob is a separate act with a
    separate consequence: it puts the document in a shared, durable, tenant-visible
    place. The user is asked first.

    Without consent this stages the file instead (see `_PENDING`) and returns. The
    agent then offers to save it, and `save_pending_artifacts` stores it on a yes.

    `consented=None` reads the per-turn consent flag, so a turn where the user said
    "yes, save it" stores immediately and no second round trip is needed.
    """
    try:
        tenant_id = get_tenant_id()
        project_id = get_project_id()
        if not tenant_id or not project_id:
            logger.debug("register_generated_file: no tenant/project in context — skip persist (%s)", filename)
            return

        if consented is None:
            from config.ws_helper import get_consequential_approved  # noqa: PLC0415

            consented = get_consequential_approved()
        if not consented:
            sid = get_session_id() or ""
            entry = {"filename": filename, "file_path": file_path, "url": url, "stage": stage}
            pend = _PENDING.setdefault(sid, [])
            # Re-generating the same filename replaces the pending entry rather than
            # queueing a duplicate the user would be asked about twice.
            pend[:] = [e for e in pend if e["filename"] != filename] + [entry]
            logger.info(
                "register_generated_file: %s generated but NOT stored — awaiting the "
                "user's go-ahead (session=%s)", filename, sid,
            )
            return

        content_type = _CONTENT_TYPES.get(os.path.splitext(filename)[1].lower())
        artifact_type = _artifact_type_for(filename)

        # Read the bytes ONCE, here, before opening a session. The file is on local
        # disk because the generating tool put it there; blob storage is where it goes
        # to become durable and tenant-isolated.
        data: bytes | None = None
        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                logger.warning("register_generated_file: unreadable %s (%s)",
                               file_path, type(exc).__name__)

        async with get_db_session_for_tenant(tenant_id) as session:
            run_id = await _get_or_create_chat_run(session, tenant_id, project_id, stage)
            if not run_id:
                return

            if data is None:
                # Nothing to upload — record what we know so the panel still lists it.
                # `blob_path=None` so the download route reports "no stored file"
                # rather than handing a dead local path to Azure.
                session.add(Artifact(
                    run_id=run_id, tenant_id=tenant_id, artifact_type=artifact_type,
                    blob_url=None, blob_path=None,
                    content_type=content_type, size_bytes=None,
                ))
            else:
                # THE CHANGE: the row now carries BLOB coordinates, not local ones.
                #
                # It used to store `blob_url=url` (a `/generated/...` static-mount URL)
                # and `blob_path=file_path` (a Windows path). Both are local, neither
                # is tenant-isolated, and `/generated` is served by StaticFiles with no
                # authentication at all — so the "blob" columns described a file anyone
                # who guessed the path could read.
                #
                # store_artifact composes `{tenant_id}/{run_id}/{artifact_type}/{file}`,
                # which is the only thing separating tenants in blob storage, and
                # degrades to `blob_url=None` when blob storage is unconfigured rather
                # than failing the generation.
                from shared.services.artifact_store import (  # noqa: PLC0415
                    get_blob_client, store_artifact,
                )
                await store_artifact(
                    session,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    artifact_type=artifact_type,
                    filename=filename,
                    data=data,
                    content_type=content_type or "application/octet-stream",
                    blob_client=get_blob_client(),
                )

        try:
            from shared.services.artifact_service import publish_artifact_ready  # noqa: PLC0415
            await publish_artifact_ready(run_id, artifact_type, tenant_id=tenant_id)
        except Exception:
            logger.debug("register_generated_file: artifact_ready notify failed", exc_info=True)

        logger.info("register_generated_file: persisted %s (%s) for run %s", filename, artifact_type, run_id)
    except Exception:
        logger.warning("register_generated_file: failed to persist %s", filename, exc_info=True)


async def save_pending_artifacts(session_id: str = "") -> tuple[list[str], list[str]]:
    """Store every file this session generated but has not yet saved.

    Returns (stored_filenames, failed_filenames). The pending list is cleared for the
    ones that stored, so a second call does not duplicate them; a failure stays pending
    so the user can retry rather than being told nothing happened and losing the file.

    `consented=True` is passed explicitly — reaching this function IS the consent, and
    re-reading the per-turn flag here would refuse the very action it was granted for
    when the user's approving message arrives on a later turn.
    """
    sid = session_id or get_session_id() or ""
    pending = list(_PENDING.get(sid, []))
    if not pending:
        return [], []

    stored: list[str] = []
    failed: list[str] = []
    for entry in pending:
        before = len(_PENDING.get(sid, []))  # noqa: F841 — readability at the call site
        try:
            await register_generated_file(
                entry["filename"], entry["file_path"], entry["url"],
                stage=entry["stage"], consented=True,
            )
            stored.append(entry["filename"])
        except Exception:  # noqa: BLE001 — register_* never raises, but never say never
            logger.warning("save_pending_artifacts: %s failed", entry["filename"], exc_info=True)
            failed.append(entry["filename"])

    remaining = [e for e in _PENDING.get(sid, []) if e["filename"] in failed]
    if remaining:
        _PENDING[sid] = remaining
    else:
        _PENDING.pop(sid, None)
    return stored, failed
