"""The Project Manager agent's documents — filed under ITS stage, as Word, with a page view.

WHAT IT DID. This agent borrowed the Design agent's `export_document`, which registers
every file under the `design` stage. An effort estimate exported from the Project Manager
page therefore landed in Design's Documents: the Plan page showed "0 artifacts", nothing
could be raised from it, and Requests & Approvals never heard of it. The model had also
chosen `.md`, so the chat's link opened raw markdown in the browser.

So this tool is the Project Manager's own: a document is written as the platform's
designed Word file with its markdown kept beside it (the page copy the app renders),
registered as a DRAFT under `plan`, and announced with its artifact id. PDF and Excel are
still available when asked for by extension; markdown and text become Word, because a
document meant for approval is read by people, not rendered by a browser as source.
"""
from __future__ import annotations

import logging
import os
import pathlib
import re
from datetime import datetime, timezone

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_FILES_DIR = str(pathlib.Path(__file__).resolve().parents[2] / "files")
_WORD_INSTEAD = (".md", ".markdown", ".txt", ".doc", "")

_H1 = re.compile(r"^\s*#\s+(?!#)(.+?)\s*#*\s*$", re.MULTILINE)
_BOLD_TITLE = re.compile(r"\A\s*\*\*(.+?)\*\*\s*(?:\n|\Z)")
_H2 = re.compile(r"^##\s+(?!#)", re.MULTILINE)


def page_markdown(content: str) -> tuple[str, str]:
    """(title, markdown) as the page and the Word canvas expect them.

    A model often writes the title as a bold line and its sections as `###`. The page
    renders `## ` headings as sections, so a document with none would be one undivided
    block: `###` is promoted when there is no `##`, and the section numbers the model
    typed are dropped (the canvas numbers sections itself)."""
    from agents_orchestrator.requirements_agent.requirements_document import normalise_headers  # noqa: PLC0415

    body = (content or "").replace("\r\n", "\n").strip()
    title = ""
    m = _H1.search(body)
    if m and body[: m.start()].strip() == "":
        title, body = m.group(1).strip(), body[m.end():].strip()
    else:
        b = _BOLD_TITLE.match(body)
        if b:
            title, body = b.group(1).strip(), body[b.end():].strip()
    if not _H2.search(body):
        body = re.sub(r"^###(\s+)", r"##\1", body, flags=re.MULTILINE)
    return title, normalise_headers(body).strip()


def _word_name(filename: str) -> str:
    from shared.tools.doc_export import normalise_filename  # noqa: PLC0415

    raw = os.path.basename((filename or "").strip())
    stem, ext = os.path.splitext(raw)
    if ext.lower() in _WORD_INSTEAD:
        raw = f"{stem or 'project_plan'}.docx"
    return normalise_filename(raw, "project_plan.docx")


@tool
async def export_document(content: str, filename: str = "project_plan.docx") -> str:
    """Export a Project Manager document — an effort estimate, a plan, a status report —
    and file it in THIS project's Plan documents as a DRAFT the user can raise for approval.

    Word by default (a designed document with a page view in the app). Ask for PDF or Excel
    by extension: "estimate.pdf", "estimate.xlsx" (Excel exports the markdown tables).
    A ".md" or ".txt" name is written as Word.

    Args:
        content: the full markdown of the document. Put the title first (a "# Title" line).
        filename: the file name, e.g. "QuickLink_Effort_Estimation.docx".
    """
    from config.connection_manager import manager  # noqa: PLC0415
    from config.env import AGENTIC_BASE_URL  # noqa: PLC0415
    from config.ws_helper import get_session_id, get_user_id  # noqa: PLC0415
    from shared.docs.markdown_docx import render_markdown_docx  # noqa: PLC0415
    from shared.services.chat_artifacts import register_generated_file  # noqa: PLC0415
    from shared.tools.doc_export import render_document, supported_list  # noqa: PLC0415

    if not (content and content.strip()):
        return "Error: nothing to export — pass the document's markdown as `content`."
    name = _word_name(filename)
    user_id, session_id = get_user_id(), get_session_id()
    out_dir = os.path.join(_FILES_DIR, str(user_id), "orchestrator", str(session_id), "output")
    os.makedirs(out_dir, exist_ok=True)
    stem, ext = os.path.splitext(name)
    n = 2
    while os.path.exists(os.path.join(out_dir, name)):
        name = f"{stem}_v{n}{ext}"
        n += 1
    path = os.path.join(out_dir, name)
    title, markdown = page_markdown(content)
    title = title or stem.replace("_", " ")

    try:
        if ext.lower() == ".docx":
            sections = [ln[3:].strip() for ln in markdown.splitlines() if ln.startswith("## ")]
            render_markdown_docx(
                markdown, path, title=title, eyebrow="PROJECT PLAN", subject="Project plan document",
                subtitle=", ".join(sections[:4]) + (" …" if len(sections) > 4 else "") if sections else "",
                meta_line=f"{datetime.now(timezone.utc):%d %b %Y} · Draft · not yet raised for approval",
                facts=[("Sections", str(len(sections)) if sections else ""),
                       ("Generated", f"{datetime.now(timezone.utc):%d %b %Y}")],
                footer=f"{title} · Project Manager",
            )
            # The page copy: what the app renders when the document is opened.
            with open(os.path.splitext(path)[0] + ".md", "w", encoding="utf-8") as fh:
                fh.write(f"# {title}\n\n{markdown}\n")
        else:
            await render_document(content, path, title=title)
    except ValueError:
        return f"Error: '{name}' has an unsupported extension. Supported: {supported_list()}"
    except Exception as exc:  # noqa: BLE001 — said, not swallowed
        logger.exception("PM export_document failed for %s", name)
        return f"Error: '{name}' could not be written ({type(exc).__name__}: {str(exc)[:200]}). Nothing was filed."

    url = f"{AGENTIC_BASE_URL}/generated/{user_id}/orchestrator/{session_id}/output/{name}"
    artifact_id = await register_generated_file(
        name, path, url, stage="plan", note="Generated by the Project Manager agent.",
    )
    if not artifact_id:
        return (f"Wrote '{name}' ({url}), but it could NOT be recorded in the project's Documents, "
                "so it cannot be raised for approval. Tell the user exactly this.")
    await manager.broadcast({
        "type": "file_generated", "session_id": session_id, "filename": name, "url": url,
        "artifact_id": artifact_id, "file_size": os.path.getsize(path),
        "agent_name": "Project Manager", "message": f"Generated file: {name}",
    })
    return (f"Exported '{name}': {url}\nIt is filed in this project's Plan documents as a DRAFT — "
            "not yet raised for approval. If the user wants it approved, call "
            f"raise_document_for_approval with \"{name}\" now; do not export it again.")
