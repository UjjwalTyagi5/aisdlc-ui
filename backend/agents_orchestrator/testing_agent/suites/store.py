"""Writing suites and reports to disk and filing them as project documents — and reading them back.

Every file is filed under the `testing` stage as a DRAFT with its page copy (the `.md`
beside it), exactly as the other agents' documents are, so it is listed in the Documents
panel, opens on the page, downloads, and can be raised for approval.
"""
from __future__ import annotations

import logging
import os
import pathlib
from typing import Any, Optional

logger = logging.getLogger(__name__)

FILES_DIR = pathlib.Path(__file__).resolve().parents[3] / "files"


def output_dir(user_id: str, job_id: str) -> str:
    d = FILES_DIR / str(user_id or "shared") / "testing" / job_id / "output"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _free_name(folder: str, name: str) -> str:
    stem, ext = os.path.splitext(name)
    n, candidate = 2, name
    while os.path.exists(os.path.join(folder, candidate)):
        candidate, n = f"{stem}_v{n}{ext}", n + 1
    return candidate


async def file_document(data: bytes, markdown: str, name: str, *, user_id: str, job_id: str,
                        note: str) -> dict:
    """Write `name` (+ its page copy) and register it. Returns {name, url, artifact_id}.
    Raises RuntimeError when it could not be recorded — a run that reports success with no
    document on file is exactly what must not happen."""
    from config.env import AGENTIC_BASE_URL  # noqa: PLC0415
    from shared.services.chat_artifacts import register_generated_file  # noqa: PLC0415

    folder = output_dir(user_id, job_id)
    name = _free_name(folder, name)
    path = os.path.join(folder, name)
    with open(path, "wb") as fh:
        fh.write(data)
    with open(os.path.splitext(path)[0] + ".md", "w", encoding="utf-8") as fh:
        fh.write(markdown)
    url = f"{AGENTIC_BASE_URL}/generated/{user_id or 'shared'}/testing/{job_id}/output/{name}"
    artifact_id = await register_generated_file(name, path, url, stage="testing", note=note)
    if not artifact_id:
        raise RuntimeError(f"'{name}' was written but could not be recorded in the project's Documents")
    return {"name": name, "url": url, "artifact_id": artifact_id}


async def document_bytes(tenant_id: str, project_id: str, artifact_id: str) -> tuple[Any, bytes]:
    """(artifact row, its bytes) for a document of THIS project. Raises LookupError with the reason."""
    import uuid as _uuid  # noqa: PLC0415

    from shared.db import get_db_session_for_tenant  # noqa: PLC0415
    from shared.models.orm import Artifact  # noqa: PLC0415
    from shared.services.artifact_store import get_blob_client, pending_blob_path  # noqa: PLC0415

    try:
        key = _uuid.UUID(str(artifact_id))
    except ValueError as exc:
        raise LookupError("that is not a document id") from exc
    async with get_db_session_for_tenant(tenant_id) as db:
        row: Optional[Artifact] = await db.get(Artifact, key)
    if row is None or str(row.project_id) != str(project_id):
        raise LookupError("no such document in this project")
    if (row.approval_status or "") == "rejected":
        raise LookupError("this document was rejected and its file deleted")
    if not row.blob_path:
        raise LookupError("this document has no stored file")
    client = get_blob_client()
    if client is None:
        raise LookupError("document storage is not configured")
    locations = [row.blob_path] if row.approval_status == "approved" else [pending_blob_path(row.blob_path), row.blob_path]
    for loc in locations:
        try:
            return row, await client.download_bytes(loc)
        except Exception:  # noqa: BLE001 — try the other location, then say so
            continue
    raise LookupError("the document's file could not be read from storage")
