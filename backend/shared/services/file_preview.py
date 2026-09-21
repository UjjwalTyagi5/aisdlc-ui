"""What a filed document looks like on the page when it has no page copy — read from the file.

`GET /artifacts/{id}/preview` answers a workbook with its sheets and a document with its page
copy. Everything else an agent files had no view at all: the page said "no page view" and
offered a download that a draft does not even have. This reads the file itself, by kind:

  .docx               its text, headings and tables    (docx_preview — "read from the Word file")
  .pptx               its slides as an outline         (slide_preview — "read from the slides")
  .csv .tsv           a workbook of one sheet          (sheet_preview)
  .md .markdown       the markdown itself
  .txt .json .yaml …  the text, as a code block
  .html .htm          the page, for a sandboxed frame  (the Testing agent's coverage report)
  .pdf and images     drawn by the browser from `GET /artifacts/{id}/preview/file`

It is only ever the FALLBACK. A document that opens today — a workbook, a page copy, a Word
file — keeps exactly the view it has; these readers run where the answer used to be 404.

Nothing here writes.
"""
from __future__ import annotations

import os
from typing import Optional

#: Formats the browser draws itself. The page fetches their bytes from `/preview/file`.
INLINE_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}

_MARKDOWN_EXTS = {".md", ".markdown"}
#: Plain text, shown as a code block tagged with its language.
_TEXT_EXTS = {".txt": "", ".log": "", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".xml": "xml"}
_HTML_EXTS = {".html", ".htm"}

#: How much text or HTML a page will show. Past this it is cut, and says so.
MAX_TEXT_CHARS = 200_000
MAX_HTML_CHARS = 5_000_000


def _ext(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def inline_type(filename: str) -> Optional[str]:
    """The media type to serve a PDF or an image with, or None for any other file."""
    return INLINE_TYPES.get(_ext(filename))


def has_file_view(filename: str) -> bool:
    """Whether the file itself can be read into a view — so its bytes are worth fetching."""
    from shared.services.docx_preview import can_preview  # noqa: PLC0415
    from shared.services.sheet_preview import can_preview_csv  # noqa: PLC0415
    from shared.services.slide_preview import can_preview_slides  # noqa: PLC0415

    ext = _ext(filename)
    return (can_preview(filename) or can_preview_slides(filename) or can_preview_csv(filename)
            or ext in _MARKDOWN_EXTS or ext in _TEXT_EXTS or ext in _HTML_EXTS)


def _cut(text: str, limit: int, what: str) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit("\n", 1)[0] + f"\n\n_The rest of this {what} is in the file._"


def _code_block(text: str, language: str) -> str:
    """Fenced with more backticks than the text itself uses, so no line can close it."""
    run, longest = 0, 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{text.rstrip()}\n{fence}"


def file_view(filename: str, data: bytes) -> Optional[dict]:
    """The view read from the file's own bytes, or None when they cannot be read as its kind.
    Call only when `has_file_view(filename)`."""
    from shared.services.docx_preview import can_preview, docx_markdown  # noqa: PLC0415
    from shared.services.sheet_preview import can_preview_csv, csv_sheets, decode_text  # noqa: PLC0415
    from shared.services.slide_preview import can_preview_slides, pptx_markdown  # noqa: PLC0415

    ext = _ext(filename)
    if can_preview(filename):
        markdown = docx_markdown(data)
        return None if markdown is None else {"kind": "markdown", "markdown": markdown, "derived": True, "derivedFrom": "word"}
    if can_preview_slides(filename):
        markdown = pptx_markdown(data)
        return None if markdown is None else {"kind": "markdown", "markdown": markdown, "derived": True, "derivedFrom": "slides"}
    if can_preview_csv(filename):
        sheets = csv_sheets(data, filename)
        return None if sheets is None else {"kind": "sheets", "sheets": sheets}

    text = decode_text(data)
    if text is None:
        return None
    if ext in _MARKDOWN_EXTS:
        return {"kind": "markdown", "markdown": _cut(text, MAX_TEXT_CHARS, "document"), "derived": False}
    if ext in _TEXT_EXTS:
        block = _code_block(text[:MAX_TEXT_CHARS], _TEXT_EXTS[ext])
        if len(text) > MAX_TEXT_CHARS:
            block += "\n\n_The rest of this file is in the download._"
        return {"kind": "markdown", "markdown": block, "derived": False}
    if ext in _HTML_EXTS:
        return {"kind": "html", "html": text[:MAX_HTML_CHARS], "truncated": len(text) > MAX_HTML_CHARS}
    return None
