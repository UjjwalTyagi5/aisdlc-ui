"""The Documentation agent's deliverables as Word documents, on the platform's canvas.

WHY. Every other agent's document — the BRD, the design, the test cases, the Code Review
and Security reports — is filed as a designed Word file with its markdown kept beside
it for the page. This agent saved bare `.md` files: the Documents panel listed a file
that opened as nothing, and a handover pack went to its approver as raw markdown. The
markdown stays (it is what a docs PR commits, and it is the page copy); the Word file
beside it is what is filed for approval.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from shared.docs.markdown_docx import render_markdown_docx

#: doc_type -> (label, eyebrow). The eyebrow is the band over the title.
_KINDS: dict[str, tuple[str, str]] = {
    "handover": ("Handover document", "HANDOVER DOCUMENT"),
    "kt": ("Knowledge transfer document", "KNOWLEDGE TRANSFER"),
    "overview": ("Overview", "SYSTEM OVERVIEW"),
    "sdd": ("Software design document", "SOFTWARE DESIGN DOCUMENT"),
    "api_reference": ("API reference", "API REFERENCE"),
    "code_summary": ("Code and change summary", "CODE & CHANGE SUMMARY"),
    "changelog": ("Changelog", "CHANGELOG"),
    "release_notes": ("Release notes", "RELEASE NOTES"),
    "rtm": ("Requirements traceability matrix", "REQUIREMENTS TRACEABILITY"),
    "run_summary": ("Run summary", "RUN SUMMARY"),
    "compliance": ("Compliance evidence pack", "COMPLIANCE EVIDENCE"),
    "runbook_update": ("Runbook update", "RUNBOOK UPDATE"),
    "knowledge_article": ("Knowledge article", "KNOWLEDGE ARTICLE"),
    "doc_set": ("Documentation set", "DOCUMENTATION SET"),
    "custom": ("Document", "DOCUMENT"),
}

_LEADING_H1 = re.compile(r"\A\s*#\s+(?!#)(.+?)\s*#*\s*(?:\n|\Z)")
_H2 = re.compile(r"^##\s+(?!#)(.+?)\s*#*\s*$", re.MULTILINE)


def kind_label(doc_type: str) -> str:
    return _KINDS.get(doc_type, _KINDS["custom"])[0]


def docx_path_for(md_path: str) -> str:
    return os.path.splitext(md_path)[0] + ".docx"


def write_documentation_docx(
    markdown: str, md_path: str, *, doc_type: str, title: str,
    project: str = "", repo: str = "", target: str = "", head_sha: str = "",
) -> str:
    """Write the designed Word document beside `md_path`; returns its path.

    The title band carries the title, so a leading `# Title` is not painted a second
    time as the first heading."""
    label, eyebrow = _KINDS.get(doc_type, _KINDS["custom"])
    body = (markdown or "").replace("\r\n", "\n")
    m = _LEADING_H1.match(body)
    if m:
        title = title or m.group(1).strip()
        body = body[m.end():]
    sections = [h.strip() for h in _H2.findall(body)]
    title = title or label
    return render_markdown_docx(
        body, docx_path_for(md_path),
        title=title, subject=label, eyebrow=eyebrow,
        subtitle=", ".join(sections[:4]) + (" …" if len(sections) > 4 else "") if sections else "",
        meta_line="Draft",
        facts=[
            ("Project", project),
            ("Repository", repo),
            ("Documented", target),
            ("Commit", head_sha[:7] if head_sha else ""),
            ("Date", datetime.now(timezone.utc).strftime("%d %b %Y")),
        ],
        footer=f"{title} · {label}",
    )
