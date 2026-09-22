"""The Deployment Readiness Report — the release assessment as a designed document.

Built from the submitted `DeploymentArtifact` — the agent's decision, readiness, risk,
gates, runbooks, IaC findings, compliance evidence and the package it staged — and never
re-guessed. The same content is the page copy the app renders and the Word file filed as
a DRAFT in the project's Documents, to be raised for approval like the Security and Code
Review reports.

WHY. The assessment lived only in the chat session's memory: a refresh of the backend
lost it, nobody could approve it, and the Deployment page had nothing to show an approver
but tabs that disappeared with the session.

Tables are single blocks (consecutive rows); the canvas prints a broken table as raw pipes.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

from shared.docs.markdown_docx import render_markdown_docx

_DECISION = {"go": "Go", "no_go": "No-go", "conditional": "Conditional go"}
_DECISION_SENTENCE = {
    "go": "This release may be deployed.",
    "no_go": "This release must not be deployed until the blocking issues are resolved.",
    "conditional": "This release may be deployed once the conditions below are met.",
}
_READINESS = {"ready": "Ready", "blocked": "Blocked", "conditional": "Conditional"}
_RISK = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low", "none": "None"}
_GATE = {"pass": "Passed", "fail": "Failed", "unknown": "Unknown", "skipped": "Skipped"}
_VIA = {"azure_pipelines": "Azure Pipelines", "github_actions": "GitHub Actions", "argocd": "Argo CD", "unknown": "Not detected"}


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


def _runbook(text: str, files: list[dict]) -> str:
    """The runbook itself. A live assessment's runbook fields read "See
    deploy/deploy-runbook.md" — a pointer to a file it had staged — and the report then
    carried no runbook at all. A short text that names a staged file is that file."""
    body = (text or "").strip()
    for f in files:
        path = f.get("path") or ""
        if path and path in body and len(body) <= len(path) + 80 and (f.get("contents") or "").strip():
            return _demote(f["contents"]) + f"\n\n*From the staged file `{path}`.*"
    return _demote(body) or "Not provided."


def _target(ctx: dict) -> str:
    branch = ctx.get("source_branch") or ""
    if (ctx.get("mode") or "branch") == "pr" and ctx.get("pr_id"):
        return f"pull request #{ctx.get('pr_id')} ({branch})"
    return f"the branch {branch}" if branch else "the prepared target"


def readiness_markdown(artifact: dict) -> str:
    ctx = artifact.get("context") or {}
    decision = artifact.get("release_decision") or "conditional"
    blocks: list[str] = []

    blocks.append("## Summary\n\n" + (_demote(artifact.get("summary") or "") or "No summary was given."))

    decision_block = f"**{_DECISION.get(decision, decision)}** — {_DECISION_SENTENCE.get(decision, '')}"
    if artifact.get("release_justification"):
        decision_block += "\n\n" + _demote(artifact["release_justification"])
    blocks.append("## Release decision\n\n" + decision_block)

    risk = artifact.get("risk_score") or "medium"
    readiness = artifact.get("readiness") or "conditional"
    lines = [f"Readiness: **{_READINESS.get(readiness, readiness)}** · Risk: **{_RISK.get(risk, risk)}**"]
    if artifact.get("risk_rationale"):
        lines.append(_demote(artifact["risk_rationale"]))
    blocks.append("## Readiness and risk\n\n" + "\n\n".join(lines))

    gates = artifact.get("gate_summary") or []
    if gates:
        blocks.append("## Release gates\n\n" + _table(
            ["Gate", "Result", "Note"],
            [[g.get("name"), _GATE.get(g.get("status") or "unknown", g.get("status")), g.get("note")] for g in gates],
        ) + "\n\nA gate that could not be read is Unknown — never counted as passed.")
    else:
        blocks.append("## Release gates\n\nNo gates were recorded for this assessment.")

    files = artifact.get("generated_files") or []
    package = "## Deployment package\n\n"
    if files:
        package += _table(["File", "Type", "Lines"], [
            [f.get("path"), f.get("language"), len((f.get("contents") or "").splitlines())] for f in files
        ])
        package += ("\n\nOpened as a deployment pull request: " + artifact["pr_url"]) if artifact.get("pr_url") else \
            "\n\nStaged for a deployment pull request; not yet opened."
    else:
        package += "No deployment files were staged."
    blocks.append(package)

    blocks.append("## Deploy runbook\n\n" + _runbook(artifact.get("deploy_runbook") or "", files))
    blocks.append("## Rollback runbook\n\n" + _runbook(artifact.get("rollback_runbook") or "", files))

    iac = artifact.get("iac_findings") or []
    blocks.append("## Infrastructure-as-code findings\n\n" + (_table(
        ["Severity", "File", "Rule", "Finding", "Remediation"],
        [[_RISK.get(f.get("severity") or "low", f.get("severity")), f.get("file"), f.get("rule"), f.get("description"), f.get("remediation")] for f in iac],
    ) if iac else "None recorded."))

    ce = artifact.get("compliance_evidence") or {}
    evidence = [
        ["Gate approvals", ", ".join(ce.get("gate_approvals") or []) or "None recorded"],
        ["Tests", ce.get("test_summary") or "Not recorded"],
        ["Security", ce.get("security_summary") or "Not recorded"],
        ["SBOM present", "Yes" if ce.get("sbom_present") else "No"],
        ["Notes", ce.get("notes") or "—"],
    ]
    blocks.append("## Compliance evidence\n\n" + _table(["Evidence", "Recorded"], evidence))

    method = [
        f"- **Assessed:** {_target(ctx)} in {ctx.get('repo_name') or 'the repository'}",
        f"- **Commit:** {(ctx.get('head_sha') or '')[:12] or 'unknown'}",
        f"- **Target environment:** {ctx.get('environment') or 'not given'}",
        f"- **Deploys via:** {_VIA.get(ctx.get('deploy_via') or 'unknown', ctx.get('deploy_via'))}",
        "- **Method:** an AI deployment agent read the repository and upstream results, staged the "
        "package and assessed readiness. Nothing was deployed: creating or running a pipeline is a "
        "separate request that a named approver accepts.",
    ]
    blocks.append("## Scope and method\n\n" + "\n".join(method))
    return "\n\n".join(blocks).strip()


def report_filename(artifact: dict) -> str:
    ctx = artifact.get("context") or {}
    slug = lambda v: re.sub(r"[^A-Za-z0-9]+", "_", v or "").strip("_")  # noqa: E731
    repo = slug(ctx.get("repo_name")) or "Repository"
    target = f"PR{ctx.get('pr_id')}" if ctx.get("pr_id") else slug(ctx.get("source_branch"))
    env = slug(ctx.get("environment"))
    sha = (ctx.get("head_sha") or "")[:7]
    return "_".join(p for p in (repo, "Deployment_Readiness", env, target, sha) if p) + ".docx"


def write_readiness_report(artifact: dict, out_dir: str) -> tuple[str, str]:
    """Write the report (.docx) and its markdown (.md) into `out_dir`. Returns both paths.
    Raises on failure — the caller records the failure on the assessment."""
    os.makedirs(out_dir, exist_ok=True)
    name = report_filename(artifact)
    stem, ext = os.path.splitext(name)
    n = 2
    while os.path.exists(os.path.join(out_dir, name)):
        name = f"{stem}_v{n}{ext}"
        n += 1
    docx_path = os.path.join(out_dir, name)
    markdown = readiness_markdown(artifact)

    ctx = artifact.get("context") or {}
    decision = artifact.get("release_decision") or "conditional"
    gates = artifact.get("gate_summary") or []
    failed = sum(1 for g in gates if g.get("status") == "fail")
    unknown = sum(1 for g in gates if g.get("status") in ("unknown", None))
    render_markdown_docx(
        markdown, docx_path,
        title=f"{ctx.get('repo_name') or 'Repository'} — Deployment readiness",
        eyebrow="DEPLOYMENT READINESS REPORT",
        eyebrow_tail=f"{ctx.get('environment') or 'environment'} · {ctx.get('source_branch') or ''}".strip(" ·"),
        subtitle=f"{_DECISION.get(decision, decision)}. {_DECISION_SENTENCE.get(decision, '')}",
        meta_line=" · ".join(p for p in (
            f"Commit {ctx.get('head_sha', '')[:7]}" if ctx.get("head_sha") else "",
            datetime.now(timezone.utc).strftime("%d %b %Y"),
            "Draft · not yet raised for approval",
        ) if p),
        facts=[
            ("Decision", _DECISION.get(decision, decision)),
            ("Readiness", _READINESS.get(artifact.get("readiness") or "", artifact.get("readiness") or "")),
            ("Risk", _RISK.get(artifact.get("risk_score") or "", artifact.get("risk_score") or "")),
            ("Gates", f"{len(gates)} · {failed} failed · {unknown} unknown" if gates else "none recorded"),
            ("Package", f"{len(artifact.get('generated_files') or [])} files"),
            ("Deploys via", _VIA.get(ctx.get("deploy_via") or "unknown", ctx.get("deploy_via") or "")),
        ],
        footer=f"{ctx.get('repo_name') or 'Repository'} · Deployment readiness report",
        subject="Deployment readiness report",
    )
    md_path = os.path.splitext(docx_path)[0] + ".md"
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(markdown + "\n")
    return docx_path, md_path
