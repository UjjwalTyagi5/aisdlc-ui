"""The Security Review Report — the scan as a designed document, on the shared canvas.

Built from the persisted `security_artifacts` — the agent's own summary, findings,
remediation plan and sign-off, and the SBOM it assembled — and never re-guessed. The
same content is the report the page renders (the markdown copy kept with the document)
and the Word file that is filed as a DRAFT in the project's Documents and can be raised
for approval and published to Confluence, exactly as a Code Review report is.

Tables are single blocks (consecutive rows); the canvas prints a broken table as raw
pipes. See code_review_agent/review_document.py, whose conventions this follows.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

from shared.docs.markdown_docx import render_markdown_docx

_SIGNOFF = {"pass": "Pass", "fail": "Fail", "conditional": "Conditional pass"}
_SIGNOFF_SENTENCE = {
    "pass": "No blocking security findings: this code may proceed.",
    "fail": "Blocking security findings must be fixed before this code proceeds.",
    "conditional": "This code may proceed once the conditions in the sign-off are met.",
}
_RISK = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low", "none": "None"}
_SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text.replace("|", "/")).strip() or "—"


def _demote(markdown: str) -> str:
    """The agent's own headings must not become numbered sections of the report."""
    out = []
    for line in (markdown or "").splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        out.append(f"### {m.group(2)}" if m else line)
    return "\n".join(out).strip()


def _table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items if i)


def _target(ctx: dict) -> tuple[str, str]:
    """(eyebrow tail, one-line description) for the scanned target."""
    branch = ctx.get("branch") or ""
    if (ctx.get("mode") or "branch") == "pr" and ctx.get("pr_id"):
        return f"PR #{ctx.get('pr_id')} · {branch}", f"pull request #{ctx.get('pr_id')} ({branch})"
    return f"Branch · {branch}", f"the branch {branch}"


def _where(f: dict) -> str:
    if f.get("package"):
        return f"{f['package']}" + (f" ({f.get('cve')})" if f.get("cve") else "")
    if f.get("file"):
        return f"{f['file']}:{f['line']}" if f.get("line") else f["file"]
    return f.get("cve") or "—"


_STATUS = {"ok": "Passed", "error": "Blocked", "unavailable": "Blocked"}


def _scanner_rows(scan: dict) -> list[list[Any]]:
    rows = []
    for sc in scan.get("scanners") or []:
        ok = sc.get("status") == "ok"
        found = sc.get("findings")
        if ok and (found or 0) > 0:
            result = "Failed"
        else:
            result = _STATUS.get(sc.get("status") or "", "Blocked") if ok or sc.get("status") in _STATUS else "Blocked"
        rows.append([sc.get("name"), sc.get("purpose"), result, found if ok else "not run",
                     sc.get("message") or (f"Ran in {sc.get('seconds', 0)} s" if ok else "")])
    return rows


#: CVE ids listed per package before "and N more" — the Worst and Vulnerabilities
#: columns already carry the count, so the cell never has to hold all of them.
_IDS_SHOWN = 6


def _id_list(items: list[dict]) -> str:
    """Whole ids only. The first report cut the list at 120 characters, mid-id ("CVE-2026")."""
    ids = sorted({i.get("id") for i in items if i.get("id")})
    more = len(ids) - _IDS_SHOWN
    return ", ".join(ids[:_IDS_SHOWN]) + (f" and {more} more" if more > 0 else "")


def _vulnerable_packages(scan: dict) -> list[list[Any]]:
    """One row per package, worst severity first — the scanner's list, unedited."""
    via = {f"{c.get('name')}@{c.get('version')}": c.get("via") or "direct dependency"
           for c in (scan.get("sbom") or {}).get("components") or []}
    groups: dict[str, list[dict]] = {}
    for v in scan.get("vulnerabilities") or []:
        groups.setdefault(f"{v.get('package')}@{v.get('installed')}", []).append(v)
    rows = []
    for key, items in groups.items():
        worst = min(items, key=lambda i: _SEV_RANK.get(i.get("severity"), 9))
        counts: dict[str, int] = {}
        for i in items:
            counts[i.get("severity") or "unknown"] = counts.get(i.get("severity") or "unknown", 0) + 1
        breakdown = ", ".join(f"{n} {sev}" for sev, n in sorted(counts.items(), key=lambda kv: _SEV_RANK.get(kv[0], 9)))
        rows.append([_SEV_RANK.get(worst.get("severity"), 9), items[0].get("package"), items[0].get("installed"),
                     (worst.get("severity") or "").title(), f"{len(items)} ({breakdown})",
                     _id_list(items), via.get(key, "direct dependency")])
    rows.sort(key=lambda r: (r[0], r[1] or ""))
    return [r[1:] for r in rows]


def security_markdown(artifact: dict) -> str:
    ctx = artifact.get("context") or {}
    findings = sorted(artifact.get("findings") or [], key=lambda f: _SEV_RANK.get(f.get("severity"), 9))
    metrics = artifact.get("metrics") or {}
    signoff = artifact.get("signoff") or {}
    sbom = artifact.get("sbom") or []
    _, target_line = _target(ctx)
    blocks: list[str] = []

    # ── 01 summary ──
    blocks.append("## Summary")
    blocks.append(_demote(artifact.get("summary") or "The agent recorded no summary for this scan."))

    # ── 02 sign-off ──
    blocks.append("## Sign-off")
    decision = signoff.get("decision") or "conditional"
    blocks.append(
        f"**{_SIGNOFF.get(decision, decision)}** — {_SIGNOFF_SENTENCE.get(decision, '')}"
        + (f"\n\n{signoff['rationale']}" if signoff.get("rationale") else "")
    )

    # ── 03 scanner results — the scanners' own, kept apart from the reviewer's triage ──
    scan = artifact.get("scan") or {}
    if scan.get("scanners"):
        blocks.append("## Scanner results")
        totals = scan.get("totals") or {}
        blocked = [sc.get("name") for sc in scan.get("scanners") or [] if sc.get("status") != "ok"]
        blocks.append(
            f"{totals.get('vulnerabilities', 0)} known vulnerabilities ({totals.get('vulnerabilities_high', 0)} high or critical), "
            f"{totals.get('secrets', 0)} hardcoded secrets, {totals.get('sast', 0)} static-analysis findings; "
            f"{totals.get('components', 0)} SBOM components."
            + (f" **Not established:** {', '.join(blocked)} did not run, so the areas it checks are unknown." if blocked else "")
        )
        blocks.append(_table(["Scanner", "What it checks", "Result", "Findings", "Note"], _scanner_rows(scan)))
        packages = _vulnerable_packages(scan)
        if packages:
            blocks.append("### Vulnerable packages")
            blocks.append(_table(["Package", "Installed", "Worst", "Vulnerabilities", "CVEs", "Brought in by"], packages))
        for label, key in (("Hardcoded secrets", "secrets"), ("Static analysis", "sast")):
            items = scan.get(key) or []
            if items:
                blocks.append(f"### {label}")
                blocks.append(_table(["Where", "Rule", "Detail"], [[
                    f"{i.get('file') or i.get('path') or ''}:{i.get('line') or i.get('start_line') or ''}",
                    i.get("rule") or i.get("rule_id") or i.get("check_id") or "",
                    i.get("message") or i.get("description") or i.get("title") or ""] for i in items[:50]]))

    # ── 04 findings — the reviewer's triage ──
    blocks.append("## Findings")
    if findings:
        blocks.append("The reviewer's findings and triage of what the scanners found and the code it read.")
        blocks.append(_table(
            ["ID", "Severity", "Category", "Finding", "Where", "Triage"],
            [[f.get("id"), (f.get("severity") or "").title(), (f.get("category") or "").replace("_", " "),
              f.get("title") or (f.get("description") or "")[:80], _where(f),
              (f.get("triage") or "unconfirmed").replace("_", " ")] for f in findings],
        ))
        blocks.append("### Finding detail")
        blocks.append(_table(
            ["ID", "Description", "Remediation", "Compliance"],
            [[f.get("id"), f.get("description"), f.get("remediation"), ", ".join(f.get("compliance") or [])]
             for f in findings],
        ))
    elif scan.get("vulnerabilities") or scan.get("secrets") or scan.get("sast"):
        blocks.append("The reviewer recorded no findings; the scanner results above stand on their own.")
    else:
        blocks.append("No findings were recorded.")

    # ── 05 remediation ──
    blocks.append("## Remediation plan")
    blocks.append(_demote(artifact.get("remediation_plan") or "No remediation plan was recorded."))

    # ── 05 supply chain ──
    notes = artifact.get("supply_chain") or []
    blocks.append("## Supply chain")
    blocks.append(
        _table(["Package", "Risk", "Note"], [[n.get("package"), (n.get("risk") or "").title(), n.get("note")] for n in notes])
        if notes else "No supply-chain concerns were recorded."
    )

    # ── 07 sbom ──
    blocks.append("## Software bill of materials")
    if sbom:
        vulnerable = [c for c in sbom if (c.get("vulnerabilities") or 0) > 0]
        components = (scan.get("sbom") or {}).get("components") or []
        direct = [c for c in components if c.get("direct")]
        blocks.append(
            f"{len(sbom)} components; {len(vulnerable)} with known vulnerabilities."
            + (f" {len(direct)} declared directly; the rest are brought in by them." if components else "")
        )
        for note in (scan.get("sbom") or {}).get("notes") or []:
            blocks.append(f"> {note}")
        if direct:
            blocks.append("### Direct dependencies")
            blocks.append(_table(
                ["Component", "Declared", "Resolved", "Licence", "Vulnerabilities"],
                [[c.get("name"), c.get("declared") or "—", c.get("version") or "—", c.get("license") or "—",
                  "not checked" if c.get("vulnerabilities") is None else c.get("vulnerabilities")]
                 for c in sorted(direct, key=lambda c: (-(c.get("vulnerabilities") or 0), c.get("name") or ""))],
            ))
        shown = sorted(sbom, key=lambda c: (-(c.get("vulnerabilities") or 0), c.get("name") or ""))
        shown = [c for c in shown if (c.get("vulnerabilities") or 0) > 0][:40]
        if shown:
            blocks.append("### Vulnerable components")
            blocks.append(_table(
                ["Component", "Version", "Licence", "Vulnerabilities"],
                [[c.get("name"), c.get("version"), c.get("license"), c.get("vulnerabilities") or 0] for c in shown],
            ))
        blocks.append(f"The full list of {len(sbom)} components, with versions and licences, is on the page's SBOM tab.")
    else:
        blocks.append("The SBOM was not built.")

    # ── 08 suppressions ──
    suppressions = artifact.get("suppression_log") or []
    if suppressions:
        blocks.append("## Suppressed findings")
        blocks.append(_table(["Finding", "Reason"], [[s.get("finding_id"), s.get("reason")] for s in suppressions]))

    # ── 09 scope ──
    blocks.append("## Scope and method")
    items = [f"**Scanned:** {target_line} in {ctx.get('repo_name') or 'the repository'}"]
    if ctx.get("head_sha"):
        items.append(f"**Commit:** {ctx['head_sha'][:12]}")
    frameworks = artifact.get("compliance_frameworks") or []
    if frameworks:
        items.append("**Compliance frameworks:** " + ", ".join(frameworks))
    items.append(
        f"**Findings:** {metrics.get('total', len(findings))} · {metrics.get('critical', 0)} critical · "
        f"{metrics.get('high', 0)} high · {metrics.get('medium', 0)} medium · {metrics.get('low', 0)} low"
    )
    if scan.get("scanners"):
        items.append("**Scanners:** " + "; ".join(
            f"{sc.get('name')} ({'ran' if sc.get('status') == 'ok' else sc.get('status')})" for sc in scan["scanners"]))
    items.append("**Method:** the scanner results and SBOM come from the scanners, unedited; the findings are an AI security reviewer's triage of them and of the code it read.")
    blocks.append(_bullets(items))
    return "\n\n".join(b for b in blocks if b)


def report_filename(artifact: dict) -> str:
    ctx = artifact.get("context") or {}
    slug = lambda v: re.sub(r"[^A-Za-z0-9]+", "_", v or "").strip("_")  # noqa: E731
    repo = slug(ctx.get("repo_name")) or "Repository"
    target = f"PR{ctx.get('pr_id')}" if ctx.get("pr_id") else slug(ctx.get("branch"))
    sha = (ctx.get("head_sha") or "")[:7]
    return "_".join(p for p in (repo, "Security_Review", target, sha) if p) + ".docx"


def write_security_report(artifact: dict, out_dir: str) -> tuple[str, str]:
    """Write the report (.docx) and its markdown (.md) into `out_dir`. Returns both paths.
    Raises on failure — the caller records the failure on the scan."""
    os.makedirs(out_dir, exist_ok=True)
    name = report_filename(artifact)
    stem, ext = os.path.splitext(name)
    n = 2
    while os.path.exists(os.path.join(out_dir, name)):
        name = f"{stem}_v{n}{ext}"
        n += 1
    docx_path = os.path.join(out_dir, name)
    markdown = security_markdown(artifact)

    ctx = artifact.get("context") or {}
    metrics = artifact.get("metrics") or {}
    findings = artifact.get("findings") or []
    decision = (artifact.get("signoff") or {}).get("decision") or "conditional"
    risk = artifact.get("risk_score") or "none"
    scan_totals = (artifact.get("scan") or {}).get("totals") or {}
    tail, _ = _target(ctx)
    render_markdown_docx(
        markdown, docx_path,
        title=f"{ctx.get('repo_name') or 'Repository'} — Security review",
        eyebrow="SECURITY REVIEW REPORT",
        eyebrow_tail=tail,
        subtitle=f"{_SIGNOFF.get(decision, decision)}. {_SIGNOFF_SENTENCE.get(decision, '')}",
        meta_line=" · ".join(p for p in (
            f"Commit {ctx.get('head_sha', '')[:7]}" if ctx.get("head_sha") else "",
            datetime.now(timezone.utc).strftime("%d %b %Y"),
            "Draft · not yet raised for approval",
        ) if p),
        facts=[
            ("Sign-off", _SIGNOFF.get(decision, decision)),
            ("Risk", _RISK.get(risk, risk)),
            ("Findings", f"{metrics.get('total', len(findings))} · {metrics.get('critical', 0)} critical · {metrics.get('high', 0)} high"),
            ("Vulnerabilities", f"{scan_totals.get('vulnerabilities', 0)} · {scan_totals.get('vulnerabilities_high', 0)} high+" if scan_totals else "not scanned"),
            ("Secrets", str(scan_totals.get("secrets", 0)) if scan_totals else "not scanned"),
            ("SBOM", f"{len(artifact.get('sbom') or [])} components"),
        ],
        footer=f"{ctx.get('repo_name') or 'Repository'} · Security review report",
        subject="Security review report",
    )
    md_path = os.path.splitext(docx_path)[0] + ".md"
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(markdown + "\n")
    return docx_path, md_path
