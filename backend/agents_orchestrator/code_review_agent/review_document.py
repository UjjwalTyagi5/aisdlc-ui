"""The Code Review agent's report: the review of a change, as a document.

A CODE REVIEW REPORT, AND ONLY THAT. It was titled "Code review & security report" and
carried the scanners' output — secrets, dependency vulnerabilities, an SBOM — which is the
SECURITY agent's report (PRD 21.5: it owns the scanning stack, the SBOM and the sign-off).
Both pages showed the same SBOM, and neither made the other redundant.

What this one answers (PRD 21.4): was the whole change read, does it do what the APPROVED
requirements say, does it follow the APPROVED design, what is wrong with it and where, and
should it merge. Security appears the way a reviewer sees it — an injection or a credential
in the code is a finding with a file and a line — never as a scan result or a verdict.

Everything here is derived from the submitted review; the document never re-judges the code.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

from shared.docs.markdown_docx import render_markdown_docx

_VERDICT = {
    "approve": "Approve",
    "request_changes": "Request changes",
    "needs_discussion": "Needs discussion",
}
_VERDICT_SENTENCE = {
    "approve": "No critical or high findings: the reviewed code is ready to merge.",
    "request_changes": "Critical or high findings must be fixed before this code is merged.",
    "needs_discussion": "Material trade-offs need a team decision before this code is merged.",
}
_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4, "warning": 2, "error": 1, "unknown": 5}
_MAX_ROWS = 60


def _cell(value: Any) -> str:
    """A table-safe cell: no pipes, no newlines."""
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text.replace("|", "/")).strip() or "—"


def _demote(markdown: str) -> str:
    """Embedded markdown (the agent's summaries) must not open its own ## sections —
    they would be numbered as sections of the report."""
    out = []
    for line in (markdown or "").splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        out.append(f"### {m.group(2)}" if m else line)
    return "\n".join(out).strip()


def _location(file: str, line: Any) -> str:
    if not file:
        return "—"
    return f"{file}:{line}" if line else file


def _target(ctx: dict) -> tuple[str, str]:
    """(eyebrow tail, one-line description) for the review target."""
    mode = ctx.get("mode") or "branch"
    src, base = ctx.get("source_branch") or "", ctx.get("base_branch") or ""
    if mode == "repo":
        return f"Whole branch · {src}", f"the whole branch {src}"
    if mode == "pr":
        return f"PR #{ctx.get('pr_id')} · {src} → {base}", f"pull request #{ctx.get('pr_id')} ({src} → {base})"
    return f"{src} vs {base}", f"the changes on {src} since it left {base}"


def _table(header: list[str], rows: list[list[Any]]) -> str:
    """One markdown table as ONE block — rows must be consecutive lines to parse."""
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items if i)


def _version_key(v: str):
    try:
        from packaging.version import Version  # noqa: PLC0415

        return (0, Version(v))
    except Exception:  # noqa: BLE001 — a non-PEP440 version sorts after, textually
        return (1, v)


def _upgrade_to(fixes: list[str]) -> str:
    """The lowest version that fixes EVERY listed vulnerability: the highest of the
    per-vulnerability minimum fixes. Trivy lists alternatives per CVE ("3.0.1, 2.0.1");
    the newest line of each is taken."""
    per_cve = []
    for fixed in fixes:
        options = [o.strip() for o in (fixed or "").split(",") if o.strip()]
        if not options:
            return ""
        per_cve.append(max(options, key=_version_key))
    return max(per_cve, key=_version_key) if per_cve else ""


def _short_title(title: str, package: str) -> str:
    """Trivy titles repeat the package ("node-tar: tar: node-tar: Arbitrary…")."""
    t = title or ""
    for _ in range(3):
        t = re.sub(rf"^\s*(?:node-)?{re.escape(package)}:\s*", "", t, flags=re.IGNORECASE)
    return t.strip()


_CHECK_OK = ("Done", "No issues")


def review_checklist(artifact: dict) -> list[dict]:
    """The standard code review checklist, answered from the submitted review. Pure.

    Each row is {check, result, detail}. A check nothing established says so ("Not run",
    "Not checked") — never "Done"."""
    findings = artifact.get("findings") or []
    scope = artifact.get("scope") or {}
    rows: list[dict] = []

    if scope.get("mode") == "repo":
        read, total = scope.get("reviewable_files_read", 0) or 0, scope.get("reviewable_files", 0) or 0
        rows.append({"check": "Whole change read", "result": "Done" if total and read >= total else "Partial",
                     "detail": f"{read} of {total} reviewable files read"})
    else:
        rows.append({"check": "Whole change read", "result": "Done",
                     "detail": f"{scope.get('files_changed', 0)} changed files reviewed"})

    # SECURITY AS THE REVIEWER SEES IT — what was judged in the code, by file and line.
    # The scan itself (dependency vulnerabilities, secrets, the SBOM, the sign-off) is the
    # SECURITY agent's report, and this checklist never speaks for it: two rows here used to
    # be filled from a scanner pass this agent ran, which is why both pages showed an SBOM.
    blocking_security = [f for f in findings if f.get("category") == "security" and f.get("severity") in ("critical", "high")]
    rows.append({"check": "Security issues in the code", "result": "Issues found" if blocking_security else "None found",
                 "detail": (f"{len(blocking_security)} critical/high security finding(s) in this review"
                            if blocking_security
                            else "no critical/high security finding in this review; the scan is the Security agent's")})

    coverage = artifact.get("requirements_coverage") or []
    if not coverage:
        rows.append({"check": "Meets the approved requirements", "result": "Not checked", "detail": "no requirements coverage recorded"})
    else:
        gaps = [c for c in coverage if c.get("status") in ("violated", "unimplemented", "partial")]
        rows.append({"check": "Meets the approved requirements", "result": "Gaps" if gaps else "Done",
                     "detail": f"{len(coverage) - len(gaps)} of {len(coverage)} acceptance criteria satisfied"})

    conformance = artifact.get("design_conformance") or []
    if not conformance:
        rows.append({"check": "Follows the approved architecture", "result": "Not checked", "detail": "no design conformance recorded"})
    else:
        off = [c for c in conformance if c.get("status") in ("violates", "drifts")]
        rows.append({"check": "Follows the approved architecture", "result": "Deviations" if off else "Done",
                     "detail": f"{len(conformance) - len(off)} of {len(conformance)} design rules conform"})

    for check, cats in (("Logic and correctness", ("logic_error",)), ("Performance", ("performance",)),
                        ("Maintainability and style", ("maintainability", "style", "design", "other"))):
        hits = [f for f in findings if f.get("category") in cats]
        rows.append({"check": check, "result": "Issues found" if hits else "No issues",
                     "detail": ", ".join(f"{f.get('id')} ({f.get('severity')})" for f in hits[:6]) or "none recorded"})

    verdict = artifact.get("merge_recommendation") or "needs_discussion"
    rows.append({"check": "Merge recommendation", "result": _VERDICT.get(verdict, verdict),
                 "detail": _VERDICT_SENTENCE.get(verdict, "")})
    return rows


def _coverage_fact(artifact: dict) -> str:
    """"5 of 6 met" — or the honest "not checked" when the review recorded none."""
    coverage = artifact.get("requirements_coverage") or []
    if not coverage:
        return "not checked"
    met = sum(1 for c in coverage if (c.get("status") or "") == "satisfied")
    return f"{met} of {len(coverage)} met"


def _conformance_fact(artifact: dict) -> str:
    conformance = artifact.get("design_conformance") or []
    if not conformance:
        return "not checked"
    conforms = sum(1 for c in conformance if (c.get("status") or "") == "conforms")
    return f"{conforms} of {len(conformance)} conform"


def review_markdown(artifact: dict) -> str:
    ctx = artifact.get("context") or {}
    findings = sorted(artifact.get("findings") or [], key=lambda f: _SEV_ORDER.get(f.get("severity", "info"), 9))
    scope = artifact.get("scope") or {}
    blocks: list[str] = []

    # ── 01 summary ──
    blocks.append("## Summary")
    blocks.append(_demote(artifact.get("summary") or "") or "The reviewer submitted no written summary.")

    # ── the standard review checklist ──
    blocks.append("## Review checklist")
    blocks.append(_table(["Check", "Result", "Detail"],
                         [[r["check"], r["result"], r["detail"]] for r in review_checklist(artifact)]))

    # ── 02 findings ──
    blocks.append("## Findings")
    if findings:
        blocks.append(_table(
            ["ID", "Severity", "Category", "Location", "Finding", "Recommendation"],
            [[f.get("id"), (f.get("severity") or "").title(), (f.get("category") or "").replace("_", " "),
              _location(f.get("file", ""), f.get("line")), f.get("description"), f.get("recommendation")]
             for f in findings],
        ))
    else:
        blocks.append("No issues were found in the reviewed code.")

    # ── 05 requirements ──
    coverage = artifact.get("requirements_coverage") or []
    blocks.append("## Requirements coverage")
    blocks.append(
        _table(["Acceptance criterion", "Status", "Note"],
               [[c.get("ac_id"), (c.get("status") or "").title(), c.get("note")] for c in coverage])
        if coverage else
        "No requirements coverage was recorded for this review."
    )

    # ── 06 design ──
    conformance = artifact.get("design_conformance") or []
    blocks.append("## Design conformance")
    blocks.append(
        _table(["Rule", "Status", "Note"],
               [[c.get("rule"), (c.get("status") or "").title(), c.get("note")] for c in conformance])
        if conformance else
        "No design conformance was recorded for this review."
    )

    # ── 07 scope ──
    blocks.append("## Scope and method")
    _, target_line = _target(ctx)
    items = [f"**Reviewed:** {target_line} in {ctx.get('repo_name') or 'the repository'}"]
    if ctx.get("head_sha"):
        items.append(f"**Commit:** {ctx['head_sha'][:12]}")
    if scope.get("mode") == "repo":
        items.append(
            f"**Coverage:** read {scope.get('reviewable_files_read', 0)} of {scope.get('reviewable_files', 0)} "
            f"reviewable files ({scope.get('lines_total', 0)} lines of code; {scope.get('files_total', 0)} files on the branch)"
        )
        if scope.get("not_read"):
            listed = ", ".join(scope["not_read"][:40])
            more = f" and {len(scope['not_read']) - 40} more" if len(scope["not_read"]) > 40 else ""
            items.append(f"**Not read:** {listed}{more}")
    else:
        items.append(f"**Changed files:** {scope.get('files_changed', 0)}; files read for context: {len(scope.get('files_read') or [])}")
    if scope.get("languages"):
        items.append("**Languages:** " + ", ".join(f"{k} ({v})" for k, v in scope["languages"].items()))
    documents = scope.get("documents") or []
    if documents:
        items.append("**Checked against:** " + "; ".join(
            f"{d.get('title')} ({d.get('stage')}"
            + ("" if d.get("outcome") == "ok" else f" — could not be read: {d.get('outcome')}")
            + ")"
            for d in documents
        ))
    if documents:
        against = " with the project's approved requirements and design documents"
    elif "documents" in scope:
        against = "; the project had no approved requirements or design document to check it against"
    else:  # a review saved before documents were recorded — claim nothing it cannot show
        against = ""
    items.append(
        f"**Method:** an AI reviewer read the code{against}. Dependency vulnerabilities, "
        "secrets, the SBOM and the security sign-off are the Security agent's report, not this one."
    )
    blocks.append(_bullets(items))
    return "\n\n".join(b for b in blocks if b)


def report_filename(artifact: dict) -> str:
    ctx = artifact.get("context") or {}
    slug = lambda v: re.sub(r"[^A-Za-z0-9]+", "_", v or "").strip("_")  # noqa: E731
    who = f"PR{ctx.get('pr_id')}" if ctx.get("mode") == "pr" and ctx.get("pr_id") else slug(ctx.get("source_branch"))
    sha = (ctx.get("head_sha") or "")[:7]
    parts = [slug(ctx.get("repo_name")) or "Repository", "Code_Review", who, sha]
    return "_".join(p for p in parts if p) + ".docx"


def write_review_report(artifact: dict, out_dir: str) -> tuple[str, str]:
    """Write the report (.docx) and its markdown (.md) into `out_dir`. Returns both paths.
    Raises on failure — the caller records the failure on the review."""
    os.makedirs(out_dir, exist_ok=True)
    name = report_filename(artifact)
    stem, ext = os.path.splitext(name)
    n = 2
    while os.path.exists(os.path.join(out_dir, name)):
        name = f"{stem}_v{n}{ext}"
        n += 1
    docx_path = os.path.join(out_dir, name)
    markdown = review_markdown(artifact)

    ctx = artifact.get("context") or {}
    findings = artifact.get("findings") or []
    scope = artifact.get("scope") or {}
    verdict = artifact.get("merge_recommendation") or "needs_discussion"
    tail, _ = _target(ctx)
    crit_high = sum(1 for f in findings if f.get("severity") in ("critical", "high"))
    if scope.get("mode") == "repo":
        coverage = f"{scope.get('reviewable_files_read', 0)} of {scope.get('reviewable_files', 0)} files"
    else:
        coverage = f"{scope.get('files_changed', 0)} changed files"
    render_markdown_docx(
        markdown, docx_path,
        title=f"{ctx.get('repo_name') or 'Repository'} — Code review",
        eyebrow="CODE REVIEW REPORT",
        eyebrow_tail=tail,
        subtitle=f"{_VERDICT.get(verdict, verdict)}. {_VERDICT_SENTENCE.get(verdict, '')}",
        meta_line=" · ".join(p for p in (
            f"Commit {ctx.get('head_sha', '')[:7]}" if ctx.get("head_sha") else "",
            datetime.now(timezone.utc).strftime("%d %b %Y"),
            "Draft · not yet raised for approval",
        ) if p),
        facts=[
            ("Verdict", _VERDICT.get(verdict, verdict)),
            ("Findings", f"{len(findings)} · {crit_high} critical/high"),
            ("Requirements", _coverage_fact(artifact)),
            ("Design", _conformance_fact(artifact)),
            ("Scope", coverage),
        ],
        footer=f"{ctx.get('repo_name') or 'Repository'} · Code review report",
        subject="Code review report",
    )
    md_path = os.path.splitext(docx_path)[0] + ".md"
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(markdown + "\n")
    return docx_path, md_path
