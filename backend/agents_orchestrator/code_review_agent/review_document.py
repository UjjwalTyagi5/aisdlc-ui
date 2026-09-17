"""The Code Review Report — a submitted review, as a designed document.

A CODE REVIEW REPORT, NOT A SECURITY REPORT. It was titled "Code review & security
report", which reads as a security audit. It is the standard review of a change: was all of
it read, were the security checks run, does it meet the approved requirements and follow the
approved architecture, what is wrong with it, and should it merge. The security scan is one
of those checks — see `review_checklist`.

WHAT IT IS BUILT FROM. The review artifact the agent submitted (summary, findings,
requirements coverage, design conformance) and the security scan the SCANNERS produced
(secrets, static analysis, vulnerable dependencies, SBOM). The document never
re-interprets either: a scanner that did not run is shown as not run, a transitive
vulnerability names the direct dependency that brings it in, and a whole-branch review
states how many of the branch's files were actually read.

SAME CANVAS as the BRD, the design document and the test case document
(`shared/docs/markdown_docx`), so the platform's documents read as one family. The
markdown is also written beside the .docx (`<name>.md`) for the page.
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
    security = artifact.get("security") or {}
    scanners = security.get("scanners") or []
    totals = security.get("totals") or {}
    scope = artifact.get("scope") or {}
    rows: list[dict] = []

    if scope.get("mode") == "repo":
        read, total = scope.get("reviewable_files_read", 0) or 0, scope.get("reviewable_files", 0) or 0
        rows.append({"check": "Whole change read", "result": "Done" if total and read >= total else "Partial",
                     "detail": f"{read} of {total} reviewable files read"})
    else:
        rows.append({"check": "Whole change read", "result": "Done",
                     "detail": f"{scope.get('files_changed', 0)} changed files reviewed"})

    if not scanners:
        rows.append({"check": "Security checks run", "result": "Not run",
                     "detail": "secrets, static analysis and dependency scans did not run"})
    else:
        not_run = [sc.get("name") for sc in scanners if sc.get("status") != "ok"]
        parts = []
        for sc in scanners:
            if sc.get("status") != "ok":
                parts.append(f"{sc.get('name')}: not run")
            elif sc.get("name") == "Trivy":
                parts.append(f"Trivy: {totals.get('vulnerabilities', 0)} vulnerabilities ({totals.get('vulnerabilities_high', 0)} high+)")
            else:
                parts.append(f"{sc.get('name')}: {sc.get('findings', 0)} findings")
        rows.append({"check": "Security checks run", "result": "Partial" if not_run else "Done", "detail": "; ".join(parts)})

    blocking_security = [f for f in findings if f.get("category") == "security" and f.get("severity") in ("critical", "high")]
    rows.append({"check": "Security issues resolved",
                 "result": "Issues found" if blocking_security or (totals.get("secrets") or 0) > 0 else "No issues",
                 "detail": (f"{len(blocking_security)} critical/high security finding(s)" if blocking_security else "no critical/high security finding")
                 + (f"; {totals.get('secrets')} hardcoded secret(s)" if (totals.get("secrets") or 0) > 0 else "")})

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


def review_markdown(artifact: dict) -> str:
    ctx = artifact.get("context") or {}
    findings = sorted(artifact.get("findings") or [], key=lambda f: _SEV_ORDER.get(f.get("severity", "info"), 9))
    security = artifact.get("security") or {}
    scanners = security.get("scanners") or []
    scope = artifact.get("scope") or {}
    ran = {sc.get("name"): sc.get("status") == "ok" for sc in scanners}
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

    # ── security checks ──
    blocks.append("## Security checks")
    if not security:
        blocks.append("The security checks did not run for this target, so nothing about its security is established.")
    else:
        if artifact.get("security_summary"):
            blocks.append(_demote(artifact["security_summary"]))
        blocks.append("### Scanners")
        rows = []
        for sc in scanners:
            ok = sc.get("status") == "ok"
            # Passed = ran, found nothing; Failed = ran, found issues; Blocked = could not
            # run (its note says why). Never "Skipped": that reads as a choice.
            result = ("Passed" if not sc.get("findings") else "Failed") if ok else "Blocked"
            note = sc.get("message") or (f"Ran in {sc.get('seconds')} s" if ok else sc.get("status"))
            rows.append([sc.get("name"), sc.get("purpose"), result, sc.get("findings") if ok else "not run", note])
        blocks.append(_table(["Scanner", "What it checks", "Result", "Findings", "Note"], rows))
        not_run = [sc.get("name") for sc in scanners if sc.get("status") != "ok"]
        if not_run:
            blocks.append(
                f"**Not established:** {', '.join(not_run)} did not run, so the areas they check are unknown — not clean."
            )

        vulns = sorted(security.get("vulnerabilities") or [], key=lambda v: _SEV_ORDER.get(v.get("severity"), 9))
        via = {(c.get("name"), c.get("version")): c.get("via")
               for c in (security.get("sbom") or {}).get("components") or []}
        blocks.append("### Vulnerable dependencies")
        if vulns:
            grouped: dict[tuple, list[dict]] = {}
            for v in vulns:
                grouped.setdefault((v.get("package"), v.get("installed")), []).append(v)
            rows = []
            for (pkg, installed), items in sorted(
                grouped.items(), key=lambda kv: (min(_SEV_ORDER.get(i.get("severity"), 9) for i in kv[1]), kv[0][0] or "")
            ):
                counts: dict[str, int] = {}
                for i in items:
                    counts[i.get("severity") or "unknown"] = counts.get(i.get("severity") or "unknown", 0) + 1
                worst = min(counts, key=lambda k: _SEV_ORDER.get(k, 9))
                breakdown = ", ".join(f"{n} {sev}" for sev, n in sorted(counts.items(), key=lambda kv: _SEV_ORDER.get(kv[0], 9)))
                rows.append([
                    pkg, installed, worst.title(), f"{len(items)} ({breakdown})",
                    _upgrade_to([i.get("fixed", "") for i in items]) or "no fix released",
                    via.get((pkg, installed)) or "direct dependency",
                ])
            blocks.append(_table(["Package", "Installed", "Severity", "Vulnerabilities", "Upgrade to", "Brought in by"], rows))
            blocks.append("#### Vulnerability detail")
            blocks.append(_table(
                ["Vulnerability", "Severity", "Package", "Issue", "Fixed in"],
                [[v.get("id"), (v.get("severity") or "").title(), f"{v.get('package')} {v.get('installed')}",
                  _short_title(v.get("title", ""), v.get("package", "")), v.get("fixed") or "—"]
                 for v in vulns[:_MAX_ROWS]],
            ))
            if len(vulns) > _MAX_ROWS:
                blocks.append(f"{len(vulns) - _MAX_ROWS} further vulnerabilities are listed on the page's Security tab.")
        else:
            blocks.append(
                "No known vulnerabilities were found in the dependencies." if ran.get("Trivy")
                else "Dependencies were not checked for vulnerabilities — the scanner did not run."
            )

        secrets = security.get("secrets") or []
        blocks.append("### Hardcoded secrets")
        if secrets:
            blocks.append(_table(
                ["Rule", "Location", "Description"],
                [[sec.get("rule"), _location(sec.get("file", ""), sec.get("line")), sec.get("description")] for sec in secrets[:_MAX_ROWS]],
            ))
            blocks.append("Secret values are redacted from this report. Rotate every exposed credential, then remove it from the code and its history.")
        else:
            blocks.append("No hardcoded secrets were detected." if ran.get("Gitleaks") else "Secrets were not scanned — the scanner did not run.")

        sast = sorted(security.get("sast") or [], key=lambda f: _SEV_ORDER.get(f.get("severity"), 9))
        blocks.append("### Static analysis")
        if sast:
            blocks.append(_table(
                ["Severity", "Rule", "Location", "Message"],
                [[(f.get("severity") or "").title(), (f.get("rule") or "").rsplit(".", 1)[-1],
                  _location(f.get("file", ""), f.get("line")), f.get("message")] for f in sast[:_MAX_ROWS]],
            ))
        else:
            blocks.append("Static analysis found no OWASP Top 10 issues." if ran.get("Semgrep") else "Static analysis did not run.")

    # ── 04 SBOM ──
    blocks.append("## Software bill of materials")
    sbom = security.get("sbom") or {}
    components = sbom.get("components") or []
    if not components:
        blocks.append("No dependency manifests were found on this branch." if security else "The SBOM was not built.")
    else:
        direct = [c for c in components if c.get("direct")]
        transitive = [c for c in components if not c.get("direct")]
        blocks.append(
            f"{len(components)} components across {len(sbom.get('manifests') or [])} manifest(s): "
            f"{len(direct)} declared directly, {len(transitive)} brought in by them."
        )
        for note in sbom.get("notes") or []:
            blocks.append(f"> {note}")
        blocks.append("### Direct dependencies")
        blocks.append(_table(
            ["Package", "Declared", "Version", "Licence", "Scope", "Vulnerabilities"],
            [[c.get("name"), c.get("declared"), c.get("version"), c.get("license"), c.get("scope"),
              "not checked" if c.get("vulnerabilities") is None else c.get("vulnerabilities")]
             for c in direct[:_MAX_ROWS]],
        ))
        flagged = [c for c in transitive if c.get("vulnerabilities")]
        if flagged:
            blocks.append("### Vulnerable transitive packages")
            blocks.append(_table(
                ["Package", "Version", "Brought in by", "Scope", "Vulnerabilities"],
                [[c.get("name"), c.get("version"), c.get("via"), c.get("scope"), c.get("vulnerabilities")] for c in flagged[:_MAX_ROWS]],
            ))
        if transitive:
            blocks.append(f"The full list of {len(transitive)} transitive packages, with versions and licences, is on the page's SBOM tab.")

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
    if scanners:
        items.append("**Scanners:** " + "; ".join(
            f"{sc.get('name')} ({'ran' if sc.get('status') == 'ok' else sc.get('status')})" for sc in scanners
        ))
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
        f"**Method:** an AI reviewer read the code{against}; the security findings and SBOM "
        "come from the scanners, not from the reviewer."
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
    security = artifact.get("security") or {}
    totals = security.get("totals") or {}
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
            ("Vulnerabilities", f"{totals.get('vulnerabilities', 0)} · {totals.get('vulnerabilities_high', 0)} high+" if security else "not scanned"),
            ("Secrets", str(totals.get("secrets", 0)) if security else "not scanned"),
            ("SBOM", f"{totals.get('components', 0)} components" if security else "not built"),
            ("Scope", coverage),
        ],
        footer=f"{ctx.get('repo_name') or 'Repository'} · Code review report",
        subject="Code review report",
    )
    md_path = os.path.splitext(docx_path)[0] + ".md"
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(markdown + "\n")
    return docx_path, md_path
