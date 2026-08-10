"""Pure helpers that turn a run's stored artifacts into a panel-ready section list.

No IO, no heavy deps — imported by both the Copilot WS (live artifact.ready) and the
runs REST API (reload/replay), so both agree on the exact artifact shape the frontend
Artifacts panel renders.

Artifact shape (matches the frontend `Artifact` type / WS contract):
    {id, stage, kind, title, content?, url?, language?}
kind ∈ markdown | mermaid | openapi | code | image | download
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Stages whose substantive turn output is a DOCUMENT (streamed to the panel, not chat).
ARTIFACT_STAGES = {"design"}

# Substrings that mark a Design-agent document (detected mid-stream to switch to the panel).
DESIGN_SIGNATURES = (
    "High-Level Design",
    "## HLD",
    "Low-Level Design",
    "C4 Architecture",
    "API Contracts",
    "Database Schema",
    "Architecture Decision Records",
    "Technology Stack",
)

# (keyword matched against a "## " header line, canonical title). First match wins.
_DESIGN_HEADER_MAP = [
    ("high-level design", "High-Level Design (HLD)"),
    ("hld", "High-Level Design (HLD)"),
    ("low-level design", "Low-Level Design (LLD)"),
    ("lld", "Low-Level Design (LLD)"),
    ("c4", "C4 Architecture Diagram"),
    ("api contract", "API Contracts"),
    ("database schema", "Database Schema"),
    ("architecture decision", "Architecture Decision Records (ADRs)"),
    ("technology stack", "Technology Stack & Infrastructure"),
]


# A design document carries its signatures as ## section HEADERS. Matching a bare
# substring anywhere in prose is a false-positive trap: a conversational reply that
# merely MENTIONS "database schema" or "API contract" (e.g. the design agent greeting
# "want the full architecture — HLD, LLD, database schema, …?") is NOT a document and
# must stay in chat, not get routed to the Artifacts panel (which also spuriously
# opened the approval gate). So require the signature to appear on a markdown header line.
_DESIGN_HEADER_SIGNATURE_RE = re.compile(
    r"(?mi)^\s{0,3}#{1,6}\s+.*(?:"
    r"high-level design|\bHLD\b|low-level design|\bLLD\b|c4 architecture|"
    r"api contract|database schema|architecture decision|technology stack"
    r")"
)


def looks_like_design_doc(text: str) -> bool:
    """True once the streamed text carries a Design-document signature AS A HEADER.

    Prose that merely mentions a section name does NOT match — only a real markdown
    section header (## High-Level Design, ## Database Schema, …) does."""
    return bool(_DESIGN_HEADER_SIGNATURE_RE.search(text))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "section"


def parse_design_markdown(md: str) -> tuple[list[dict], dict]:
    """Split a Design document into panel sections + a persistence dict.

    Returns (sections, persist). `sections` is the panel-ready list (one per ## header,
    each kind="markdown" so code fences / mermaid / images render inside MarkdownMessage).
    `persist` is what we store on runs.design_artifacts: the sections plus the raw
    markdown and best-effort DesignArtifact-shaped fields for downstream consumers."""
    if not md or not md.strip():
        return [], {}
    # Split on level-2 headers, keeping the header text.
    parts = re.split(r"(?m)^##\s+(.+?)\s*$", md)
    # parts = [pre, header1, body1, header2, body2, ...]
    sections: list[dict] = []
    fields: dict[str, Any] = {}
    i = 1
    seen = 0
    while i + 1 < len(parts) + 1 and i < len(parts):
        header = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        i += 2
        if not header:
            continue
        canonical = header
        key = None
        low = header.lower()
        for kw, title in _DESIGN_HEADER_MAP:
            if kw in low:
                canonical = title
                key = title
                break
        seen += 1
        sec = {
            "id": f"design-{seen}-{_slug(canonical)}",
            "stage": "design",
            "kind": "markdown",
            "title": canonical,
            "content": f"## {header}\n\n{body}" if body else f"## {header}",
        }
        sections.append(sec)
        # Best-effort field mapping for durability / downstream typed consumers.
        if "high-level" in low or low.strip() == "hld":
            fields["hld"] = body
        elif "low-level" in low or low.strip() == "lld":
            fields["lld"] = body
        elif "api contract" in low:
            fields["api_contracts"] = body
        elif "database schema" in low:
            fields["database_schema"] = body
        elif "c4" in low:
            fields["c4_diagram"] = body
        elif "architecture decision" in low:
            fields["adrs_md"] = body
        elif "technology stack" in low:
            fields["tech_stack"] = body

    persist = {"sections": sections, "markdown": md, **fields}
    return sections, persist


def _requirements_sections(payload: dict) -> list[dict]:
    """Render a requirements_payload as a single readable markdown artifact."""
    if not isinstance(payload, dict):
        return []
    project = payload.get("project") or "Requirements"
    stories = payload.get("stories") or []
    lines = [f"# Requirements — {project}", ""]
    if payload.get("scope_summary"):
        lines += [f"**Scope:** {payload['scope_summary']}", ""]
    for s in stories:
        title = s.get("title") or f"Story {s.get('id', '')}"
        lines.append(f"## {title}")
        acs = s.get("acceptance_criteria") or s.get("acceptanceCriteria") or []
        if isinstance(acs, str):
            acs = [acs]
        for ac in acs:
            lines.append(f"- {ac}")
        lines.append("")
    return [{
        "id": "requirements-1",
        "stage": "requirements",
        "kind": "markdown",
        "title": f"Requirements — {project}",
        "content": "\n".join(lines),
    }]


def sections_from_run(run: Any) -> list[dict]:
    """Build the full artifact list from a run's persisted artifact columns.

    Used by the REST read so a reopened run replays its artifacts. Order: earliest
    stage first. Fail-soft: unknown/empty columns are skipped."""
    out: list[dict] = []

    req = getattr(run, "requirements_payload", None)
    if isinstance(req, dict) and req:
        out += _requirements_sections(req)

    req_arts = getattr(run, "requirements_artifacts", None)
    if isinstance(req_arts, dict) and req_arts:
        secs = req_arts.get("sections")
        if isinstance(secs, list) and secs:
            out += secs
        if req_arts.get("has_files"):
            out.append(stage_files_section("requirements"))

    design = getattr(run, "design_artifacts", None)
    if isinstance(design, dict) and design:
        secs = design.get("sections")
        if isinstance(secs, list) and secs:
            out += secs
        elif design.get("markdown"):
            secs2, _ = parse_design_markdown(design["markdown"])
            out += secs2

    dev = getattr(run, "development_artifacts", None)
    if isinstance(dev, dict) and dev:
        out += _development_sections(dev)

    # Code Review / Security / Testing / Deployment / Documentation persist their
    # report as {stage}_artifacts = {"sections": [...]}; render them uniformly.
    for stage, col in (
        ("code_review", "code_review_artifacts"),
        ("security", "security_artifacts"),
        ("testing", "testing_artifacts"),
        ("deployment", "deployment_artifacts"),
        ("documentation", "documentation_artifacts"),
    ):
        val = getattr(run, col, None)
        if isinstance(val, dict) and val:
            secs = val.get("sections")
            if isinstance(secs, list) and secs:
                out += secs
            if val.get("has_files"):
                out.append(stage_files_section(stage))

    return out


def stage_files_section(stage: str) -> dict:
    """The file-tree artifact for a downstream stage's generated-file capture (Task 2).

    Mirrors the `dev-code` code-tree section in `_development_sections`: the frontend
    resolves the file listing against the run workspace by `stage`/`source`, this just
    supplies the pointer so reload/replay shows the same tree the live path streamed."""
    return {
        "id": f"{stage}-files",
        "stage": stage,
        "kind": "file-tree",
        "title": "Generated files",
        "source": stage,
    }


def _development_sections(dev: dict) -> list[dict]:
    """Development artifacts: the live repo code tree (code-tree kind, rendered by the
    panel against /runs/{id}/workspace/*), a code-change summary, and the PR link."""
    out: list[dict] = [{
        "id": "dev-code",
        "stage": "development",
        "kind": "code-tree",   # frontend renders RepoFileTree + CodeViewer from the run workspace
        "title": "Repository code",
    }]
    summary = dev.get("code_summary")
    if summary:
        out.append({
            "id": "dev-summary", "stage": "development", "kind": "markdown",
            "title": "Implementation Summary", "content": str(summary),
        })
    pr_url = dev.get("pr_url")
    if pr_url:
        out.append({
            "id": "dev-pr", "stage": "development", "kind": "link",
            "title": "Pull Request", "url": str(pr_url),
        })
    return out
