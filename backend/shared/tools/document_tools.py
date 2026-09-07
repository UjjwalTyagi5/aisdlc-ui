"""Shared file-reading utilities used by multiple agents.

Import `extract_file_text` here and remove the per-agent copies of
`_extract_file_text` (planning.py) / `_extract_text_from_path` (architecture.py).
"""
from __future__ import annotations

import os


#: What this function returns INSTEAD of text when it could not extract any. These read
#: like content to a caller that only checks for a non-empty string, and that is exactly
#: how a screenshot ended up announced to an agent as "the user attached this, use its
#: content directly" with the content being the words "[Binary file: shot.png]". The
#: agent then tried to open the path and dead-ended on "local file not found".
#:
#: Callers must use `extraction_succeeded` rather than truthiness.
_PLACEHOLDER_PREFIXES = ("[Binary file:", "[Error reading ")


def extraction_succeeded(text: str) -> bool:
    """True when `extract_file_text` returned real content rather than a placeholder.

    `if text:` is NOT good enough and never was: both failure modes return a non-empty
    human-readable sentence, which is the right thing to SHOW a person and the wrong
    thing to hand a model as though it were the document.
    """
    t = (text or "").strip()
    return bool(t) and not t.startswith(_PLACEHOLDER_PREFIXES)


def extract_file_text(file_path: str) -> str:
    """Return plain text from any supported file type.

    Supports: .pdf, .docx, .doc, .txt, .md, .csv, .xlsx, .xls
    Returns an error string (not an exception) so callers get a readable message —
    check it with `extraction_succeeded` before treating the result as content.
    """
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".pdf":
            import PyPDF2
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                return "\n".join(page.extract_text() or "" for page in reader.pages)

        elif ext in (".docx", ".doc"):
            from docx import Document
            doc = Document(file_path)
            return "\n".join(p.text for p in doc.paragraphs)

        elif ext in (".txt", ".md"):
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()

        elif ext == ".csv":
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()

        elif ext in (".xlsx", ".xls"):
            import pandas as pd
            df = pd.read_excel(file_path)
            return df.to_string(index=False)

        else:
            return f"[Binary file: {os.path.basename(file_path)}]"

    except Exception as exc:
        return f"[Error reading {os.path.basename(file_path)}: {exc}]"


#: Per-file cap on injected text. Long enough for a BRD or an architecture doc, short
#: enough that several attachments cannot exhaust a context window between them.
_ATTACHMENT_TEXT_LIMIT = 20_000


def attachment_message_contents(paths: "list[str] | None") -> "list[str]":
    """Turn attached file paths into message contents an agent can act on.

    Returns at most two strings: one carrying the extracted text of everything that
    could be read, and one naming what could not. Callers wrap each in a HumanMessage.
    Returns [] when there is nothing attached — the caller appends nothing.

    WHY SERVER-SIDE EXTRACTION. Passing only paths and relying on the agent to call a
    file tool loses documents silently: the tool call can be skipped, or fail on a
    Windows path mangled through the model's JSON. Reading here means an attachment
    the user can SEE in the transcript is text the model actually has.

    EXTRACTED AND MERELY NON-EMPTY ARE DIFFERENT THINGS — see `extraction_succeeded`.
    A failed extraction returns a readable placeholder ("[Binary file: shot.png]"),
    so a truthiness check announces a screenshot to the agent as document content.

    This was copied verbatim into the Requirements and Design routes, and the Plan
    route — which never had it — silently dropped every attachment: the file appeared
    in the transcript as a chip and the agent answered "I don't see a BRD attached".
    A third copy would have been a third chance to omit one, so it lives here.
    """
    if not paths:
        return []

    parts: list[str] = []
    unreadable: list[str] = []
    for path in paths:
        if not path:
            continue
        try:
            text = extract_file_text(path)
        except Exception:  # noqa: BLE001 — best-effort; an unreadable file is reported
            text = ""
        if extraction_succeeded(text):
            parts.append(
                f"--- Attached file: {os.path.basename(path)} ---\n"
                f"{text.strip()[:_ATTACHMENT_TEXT_LIMIT]}"
            )
        else:
            unreadable.append(path)

    contents: list[str] = []
    if parts:
        contents.append(
            "The user attached the following file(s); use their content directly:\n\n"
            + "\n\n".join(parts)
        )
    if unreadable:
        # NAME THE LIMIT rather than hand over a path. The old hint ("please use the
        # following files <path>") pointed the agent at a tool that cannot parse an
        # image either, so a successful upload produced "local file not found" — the
        # least useful true statement available.
        names = ", ".join(os.path.basename(u) for u in unreadable)
        contents.append(
            f"The user attached {names}, which could not be read as text — it is an "
            "image or an unsupported format. You CANNOT open it: do not call a file "
            "tool on it, and do not claim to have looked at it. Tell the user you "
            "cannot read that file type and ask them to paste the relevant text, or "
            "re-upload as .pdf, .docx, .txt, .md, .csv or .xlsx."
        )
    return contents


def attachment_paths_from_context(pipeline_context: object) -> "list[str]":
    """Paths of chat attachments carried on `pipeline_context.attachments`.

    Chat uploads go through POST /conversations/{id}/attachments and arrive as
    `[{"path": ...}, ...]`; this is the one place that shape is decoded.
    """
    if not isinstance(pipeline_context, dict):
        return []
    attachments = pipeline_context.get("attachments") or []
    return [
        a.get("path") for a in attachments
        if isinstance(a, dict) and a.get("path")
    ]
