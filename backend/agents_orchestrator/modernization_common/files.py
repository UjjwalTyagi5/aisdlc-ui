"""Where a Track 3 agent writes a document, and how the document reaches people.

`{FILES}/<user>/<segment>/<session>/output/<file>` — the same layout the Requirements
agent uses (`requirements_agent/agents/planning.py::broadcast_file_generated`), so the
`/generated` static mount serves it, and the Orchestrator's Deliverables panel finds it
through `shared/routers/runs.py::_run_stage_output_dir` (on an Orchestrator turn the
session id IS the run id).

Announcing a file does two things, both best-effort — a document the user can already
download must never be lost to a failure in either:

  1. a `file_generated` socket event, which the chat renders as a download link;
  2. `register_generated_file(..., stage=...)`, which records it as a DRAFT artifact of
     the agent's own stage. Putting it forward for approval — the Sign-off — is the
     existing artifact submit/approve flow, owned by the stage's owner.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def output_dir(segment: str) -> str:
    from config import sdlcSettings  # noqa: PLC0415
    from config.ws_helper import get_session_id, get_user_id  # noqa: PLC0415

    path = os.path.join(
        sdlcSettings().FILES, str(get_user_id() or "anonymous"), segment,
        str(get_session_id() or "session"), "output",
    )
    os.makedirs(path, exist_ok=True)
    return path


def generated_url(segment: str, filename: str) -> str:
    from config.env import AGENTIC_BASE_URL  # noqa: PLC0415
    from config.ws_helper import get_session_id, get_user_id  # noqa: PLC0415

    return (
        f"{AGENTIC_BASE_URL}/generated/{get_user_id() or 'anonymous'}/{segment}/"
        f"{get_session_id() or 'session'}/output/{filename}"
    )


async def announce_generated_file(segment: str, filename: str, path: str, *, stage: str) -> str:
    """Tell the chat about `path` and record it as a draft artifact. Returns the URL."""
    from config.connection_manager import manager  # noqa: PLC0415
    from config.ws_helper import get_session_id  # noqa: PLC0415

    url = generated_url(segment, filename)
    try:
        await manager.broadcast({
            "type": "file_generated",
            "session_id": get_session_id(),
            "filename": filename,
            "url": url,
            "file_size": os.path.getsize(path) if os.path.exists(path) else 0,
            "message": f"Generated file: {filename}",
        })
    except Exception:  # noqa: BLE001 — the file exists; the notice is a nicety
        logger.warning("file_generated broadcast failed for %s", filename)
    try:
        from shared.services.chat_artifacts import register_generated_file  # noqa: PLC0415

        await register_generated_file(filename, path, url, stage=stage)
    except Exception:  # noqa: BLE001 — never costs the user the document
        logger.warning("register_generated_file failed for %s (stage=%s)", filename, stage)
    return url
