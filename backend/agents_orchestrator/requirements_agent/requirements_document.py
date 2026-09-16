"""The Requirements agent's documents — BRD, PDD, risk register — on the platform's canvas.

WHY. The agent's BRD was written by a generic markdown→Word converter: Word's
default "Heading 1", "Table Grid", Courier for code, no title, no context — the
first document a client sees from this platform, and the only one that looked like
nobody designed it. The Track 3 brief, the Design agent's document and the Testing
agent's test case document all paint on `shared/docs/markdown_docx`; this makes the
Requirements agent's documents the fourth, in the same family.

WHAT THE PAGE NEEDS TOO. Beside the .docx this writes the document's markdown
source (`<name>.md`) so the Requirements page can render the same document as a
report in its centre panel — hero, key facts, numbered sections — rather than offer
a download link and nothing else. The .md is a sibling for the page, not a second
artifact; only the Word file is filed for approval.

THE KIND IS READ OFF THE DOCUMENT. The generation prompts fix the section
headers (BRDPROMPT: "Executive Summary", "Project Objectives", …; the PDD and risk
prompts likewise), so the headers say what the document is better than the file
name the model chose for it.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

from shared.docs.markdown_docx import render_markdown_docx

_H2_RE = re.compile(r"^##\s*(?!#)(.+?)\s*#*\s*$", re.MULTILINE)
_H1_RE = re.compile(r"^#\s*(?!#)(.+?)\s*#*\s*$", re.MULTILINE)
_NUMBERED_HEADER_RE = re.compile(r"^(#{1,6})\s*(\d+(?:\.\d+)*)\.?\s+(.+?)\s*$")
_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def normalise_headers(markdown: str) -> str:
    """The prompts number their headers ("##1. Executive Summary", "## 3.6 Risks");
    the canvas numbers sections itself. Drop the prompt's numbers, and make a dotted
    sub-number ("## 1.1 Introduction") the sub-heading it is."""
    out = []
    for line in (markdown or "").splitlines():
        m = _NUMBERED_HEADER_RE.match(line)
        if not m:
            out.append(line)
            continue
        hashes, number, title = m.groups()
        if len(hashes) == 2 and "." in number.rstrip("."):
            hashes = "###"
        out.append(f"{hashes} {title}")
    return "\n".join(out)


def _header_texts(markdown: str) -> list[str]:
    """Section headers and table column headers, lower-cased — the risk register is
    a single table, so its columns are what name it."""
    text = normalise_headers(markdown)
    found = [h.strip().lower() for h in _H2_RE.findall(text)]
    lines = text.splitlines()
    for i, line in enumerate(lines[:-1]):
        if line.strip().startswith("|") and _TABLE_SEP_RE.match(lines[i + 1].strip()):
            found.extend(c.strip().lower() for c in line.strip().strip("|").split("|"))
    return found


@dataclass(frozen=True)
class DocumentKind:
    id: str
    eyebrow: str
    label: str
    #: headers that mark this kind, lower-cased; two hits decide it
    markers: tuple[str, ...]


KINDS: tuple[DocumentKind, ...] = (
    DocumentKind("prd", "PRODUCT REQUIREMENTS DOCUMENT", "Product requirements",
                 ("product overview", "problem statement", "target users", "goals and success metrics",
                  "features", "functional requirements", "non-functional requirements", "user journeys",
                  "release plan")),
    DocumentKind("brd", "BUSINESS REQUIREMENTS DOCUMENT", "Business requirements",
                 ("executive summary", "project objectives", "needs statement", "project scope", "requirements",
                  "project constraints", "key stakeholders", "schedule", "glossary")),
    DocumentKind("pdd", "PROCESS DEFINITION DOCUMENT", "Process definition",
                 ("solution design details", "scope of requirement", "process functional description", "data flow",
                  "success criteria", "in-scope", "out of scope", "assumptions", "dependencies", "intended audience",
                  "process overview", "as-is process", "to-be process")),
    DocumentKind("risk", "RISK REGISTER", "Risk register",
                 ("risk register", "risk id", "risk description", "likelihood", "risk level", "mitigation", "owner")),
)
GENERIC = DocumentKind("document", "REQUIREMENTS DOCUMENT", "Requirements document", ())


def document_kind(markdown: str, filename: str = "") -> DocumentKind:
    """The kind, by its section headers first, then by the file name."""
    headers = _header_texts(markdown)
    best, best_hits = GENERIC, 1
    for kind in KINDS:
        # a marker names a header by its start: "scope of requirement(s)", "mitigation
        # strategy / action" — never by a word inside it, or "requirements" would be
        # everywhere
        hits = sum(1 for m in kind.markers if any(h.startswith(m) for h in headers))
        if hits > best_hits:
            best, best_hits = kind, hits
    if best is GENERIC:
        name = (filename or "").lower()
        by_id = {k.id: k for k in KINDS}
        # By id, not position: a kind added at the front must not turn every "_BRD" file
        # into something else. "prd" before "requirement", which a PRD's name may contain.
        if "prd" in name:
            return by_id["prd"]
        if "brd" in name or "requirement" in name:
            return by_id["brd"]
        if "pdd" in name or "process" in name:
            return by_id["pdd"]
        if "risk" in name:
            return by_id["risk"]
    return best


@dataclass
class RequirementsDocMeta:
    title: str
    kind: DocumentKind = GENERIC
    project: str = ""
    sources: list[str] = field(default_factory=list)
    generated_on: str = ""
    status: str = "Awaiting approval"


def title_for(markdown: str, filename: str, kind: DocumentKind, project: str) -> str:
    """A `# Title` in the document wins; else the project and the kind; else the
    file name, de-snaked."""
    m = _H1_RE.search(markdown or "")
    if m:
        return m.group(1).strip()
    if project:
        return f"{project} — {kind.label}"
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    return re.sub(r"[_\-]+", " ", stem).strip() or kind.label


def _sections(markdown: str) -> list[str]:
    return [h.strip() for h in _H2_RE.findall(normalise_headers(markdown))]


def render_requirements_docx(markdown: str, path: str, *, meta: RequirementsDocMeta,
                             render_mermaid=None, fetch_image=None) -> str:
    """Write the designed Word document at `path`; returns the path."""
    markdown = normalise_headers(markdown)
    sections = _sections(markdown)
    return render_markdown_docx(
        markdown, path,
        title=meta.title, subject=meta.kind.label,
        eyebrow=meta.kind.eyebrow, eyebrow_tail="",
        subtitle=", ".join(sections[:4]) + (" …" if len(sections) > 4 else "") if sections else "",
        meta_line=meta.status,
        facts=[
            ("Project", meta.project),
            ("Source", ", ".join(meta.sources)),
            ("Sections", str(len(sections)) if sections else ""),
            ("Generated", meta.generated_on),
        ],
        footer=f"{meta.title} · {meta.kind.label}",
        render_mermaid=render_mermaid, fetch_image=fetch_image,
    )


def write_markdown_sibling(markdown: str, docx_path: str) -> str:
    """The document's source beside the .docx, for the page's report view."""
    md_path = os.path.splitext(docx_path)[0] + ".md"
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(normalise_headers((markdown or "").replace("\r\n", "\n")).strip() + "\n")
    return md_path


def today() -> str:
    return datetime.now(timezone.utc).strftime("%d %b %Y")


def source_names(paths: Iterable[str]) -> list[str]:
    return [os.path.basename(p) for p in paths if p]
