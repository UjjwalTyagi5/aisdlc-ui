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
