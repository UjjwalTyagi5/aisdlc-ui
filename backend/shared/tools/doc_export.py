"""One place that turns Markdown into whatever file the user asked for.

WHY A DISPATCHER RATHER THAN FOUR TOOLS PER AGENT. Each agent would otherwise need a
tool per format — eight across the two, each with its own path handling and its own
chance to disagree about where files go. Worse for the model too: the Requirements agent
already binds 40-odd tools, and the more near-identical ones it sees the more often it
picks the wrong one. One tool whose behaviour follows the file extension is both smaller
and easier to choose correctly, because the user names the extension anyway.

FORMAT COMES FROM THE EXTENSION, deliberately. "make it a PDF" ends up as
report.pdf, and there is then exactly one source of truth for what to render.
"""
from __future__ import annotations

import logging
import os
from typing import Iterable

logger = logging.getLogger(__name__)

SUPPORTED = (".docx", ".pdf", ".xlsx", ".md", ".txt")


def supported_list() -> str:
    return ", ".join(SUPPORTED)


def normalise_filename(filename: str, default: str) -> str:
    """A safe leaf name with a supported extension.

    `os.path.basename` because the model picks the filename and a path separator in it
    would write outside the session's output directory.
    """
    name = os.path.basename((filename or "").strip()) or default
    if not name.lower().endswith(SUPPORTED):
        name += os.path.splitext(default)[1] or ".docx"
    return name


async def render_document(content: str, full_path: str, title: str = "") -> str:
    """Write `content` to `full_path`, choosing the renderer from the extension.

    Raises on an unsupported extension and on a genuine render failure — a tool that
    reports success without producing a file is how an agent tells a user their
    document is ready when it is not.
    """
    ext = os.path.splitext(full_path)[1].lower()
    os.makedirs(os.path.dirname(os.path.abspath(full_path)) or ".", exist_ok=True)

    if ext == ".pdf":
        from shared.tools.pdf_render import markdown_to_pdf  # noqa: PLC0415

        markdown_to_pdf(content, full_path, title=title)
    elif ext == ".xlsx":
        from shared.tools.xlsx_render import markdown_to_xlsx  # noqa: PLC0415

        markdown_to_xlsx(content, full_path, sheet_title=title)
    elif ext == ".docx":
        from shared.tools.docx_tools import markdown_to_docx  # noqa: PLC0415

        await markdown_to_docx(content, full_path)
    elif ext in (".md", ".txt"):
        with open(full_path, "w", encoding="utf-8") as fh:
            fh.write(content)
    else:
        raise ValueError(f"Unsupported format '{ext}'. Supported: {supported_list()}")

    if not os.path.exists(full_path) or os.path.getsize(full_path) == 0:
        raise RuntimeError(f"Renderer produced no output for {os.path.basename(full_path)}")
    return full_path


def export_result_message(filename: str, url: str, extras: Iterable[str] = ()) -> str:
    """The reply shape both agents use after producing a file.

    States that the file is NOT yet in the project's artifacts. Generation and
    publication are separate acts (see chat_artifacts.register_generated_file), and an
    agent that says "saved" for the first one teaches the user to expect the second.
    """
    parts = [f"Generated '{filename}'."]
    if url:
        parts.append(f"Download it here: {url}")
    parts.extend(extras)
    parts.append(
        "It is NOT yet saved to the project's artifacts — ask the user whether to save "
        "it there, and call save_to_project_artifacts if they agree."
    )
    return " ".join(parts)
