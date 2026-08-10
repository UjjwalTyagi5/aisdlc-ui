"""Chat attachment storage — per-conversation files in the work dir (§11A.6 seam).

Files are written under `files/{user_id}/attachments/{session_id}/` so every upload is
traceable to its ConversationSession and downloadable via the `/generated` static mount
(process_api mounts FILES_DIR there). This module is the single home for the path scheme
+ validation; swapping to Azure Blob later is a change here only.
"""
from __future__ import annotations

import mimetypes
import pathlib
from typing import TypedDict
from urllib.parse import quote

# Work-dir root that process_api serves at /generated (== backend/files).
_FILES_ROOT = pathlib.Path(__file__).resolve().parents[2] / "files"

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB per file
ALLOWED_ATTACHMENT_EXTS = {
    ".pdf", ".docx", ".doc", ".txt", ".md", ".xlsx", ".xls", ".csv",
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
}


class AttachmentRef(TypedDict):
    name: str
    url: str
    content_type: str
    size: int
    path: str


class AttachmentError(ValueError):
    """Raised on a rejected upload (bad extension / too large). Routers map to HTTP 400."""


def _safe_name(filename: str) -> str:
    """Strip any path components; keep just the base name."""
    return pathlib.Path(filename or "upload").name or "upload"


def _session_dir(user_id: str, session_id: str) -> pathlib.Path:
    # Keep ids as path-safe leaf names (they are uuids/handles, but be defensive).
    safe_user = pathlib.Path(user_id or "anon").name
    safe_session = pathlib.Path(session_id or "session").name
    return _FILES_ROOT / safe_user / "attachments" / safe_session


def _to_ref(user_id: str, session_id: str, path: pathlib.Path) -> AttachmentRef:
    safe_user = pathlib.Path(user_id or "anon").name
    safe_session = pathlib.Path(session_id or "session").name
    rel = f"{safe_user}/attachments/{safe_session}/{path.name}"
    url = "/generated/" + "/".join(quote(seg) for seg in rel.split("/"))
    return AttachmentRef(
        name=path.name,
        url=url,
        content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        size=path.stat().st_size if path.exists() else 0,
        path=str(path),
    )


def save_attachment(user_id: str, session_id: str, filename: str, data: bytes) -> AttachmentRef:
    """Validate + write one attachment; return its reference (incl. /generated download URL)."""
    name = _safe_name(filename)
    ext = pathlib.Path(name).suffix.lower()
    if ext not in ALLOWED_ATTACHMENT_EXTS:
        raise AttachmentError(
            f"File type '{ext or '(none)'}' is not accepted. "
            f"Allowed: {', '.join(sorted(ALLOWED_ATTACHMENT_EXTS))}"
        )
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentError(
            f"File '{name}' is too large ({len(data) // (1024 * 1024)} MB). Max 10 MB."
        )
    target_dir = _session_dir(user_id, session_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    target.write_bytes(data)
    return _to_ref(user_id, session_id, target)


def list_attachments(user_id: str, session_id: str) -> list[AttachmentRef]:
    """All attachments for a session (download view). Empty when none / on error."""
    d = _session_dir(user_id, session_id)
    if not d.is_dir():
        return []
    return [
        _to_ref(user_id, session_id, p)
        for p in sorted(d.iterdir())
        if p.is_file()
    ]
