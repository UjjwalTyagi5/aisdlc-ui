"""Shared file-reading utilities used by multiple agents.

Import `extract_file_text` here and remove the per-agent copies of
`_extract_file_text` (planning.py) / `_extract_text_from_path` (architecture.py).
"""
from __future__ import annotations

import os


def extract_file_text(file_path: str) -> str:
    """Return plain text from any supported file type.

    Supports: .pdf, .docx, .doc, .txt, .md, .csv, .xlsx, .xls
    Returns an error string (not an exception) so callers get a readable message.
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
