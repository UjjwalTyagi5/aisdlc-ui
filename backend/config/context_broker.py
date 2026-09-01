"""Context Broker — builds structured context strings for each pipeline agent.

Single public API: ``build_context(session_id, agent_id) -> str``

Reads AGENT_REGISTRY to know which artifact fields the target agent needs,
fetches them from AgentSession via fetch_session_artifacts, and formats them
into a context block the agent appends to its system prompt.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from config.agent_registry import AGENT_REGISTRY
from config.orchestrator_state_client import fetch_session_artifacts


# ── Artifact formatters ───────────────────────────────────────────────────────

def _fmt_requirements(req: Dict[str, Any]) -> str:
    project = req.get("project", "")
    provider_kind = req.get("provider_kind", "azure_devops")
    # Stories live under "stories" (board shape) or "user_stories" (artifact shape).
    stories = req.get("stories") or req.get("user_stories") or []
    nfrs = req.get("non_functional_requirements", [])
    gap = req.get("gap_report", "")
    brd = req.get("brd_content", "")
    risks = req.get("risk_register") or []

    header = f"[REQUIREMENTS CONTEXT — Project: {project} | PM Provider: {provider_kind}]"
    lines = [header]
    if brd:
        lines.append(f"BRD: {str(brd)[:600]}")
    if stories:
        lines.append(f"User Stories ({len(stories)}):")
        for s in stories[:20]:
            title = s.get("title", "") if isinstance(s, dict) else str(s)
            ac = s.get("acceptance_criteria", "") if isinstance(s, dict) else ""
            lines.append(f"  - {title}" + (f"\n    AC: {ac}" if ac else ""))
    if nfrs:
        lines.append(f"Non-Functional Requirements: {', '.join(str(n) for n in nfrs)}")
    if risks:
        lines.append(f"Risk Register ({len(risks)}):")
        for r in risks[:10]:
            if isinstance(r, dict):
                lines.append(f"  - {r.get('risk', r)} → {r.get('mitigation', '')}")
            else:
                lines.append(f"  - {r}")
    if gap:
        lines.append(f"Gap Report: {str(gap)[:500]}")
    return "\n".join(lines)


def _fmt_design(design: Dict[str, Any]) -> str:
    lines = ["[DESIGN CONTEXT]"]
    # Accept either the C4 url key (DesignArtifact) or the WS/REST diagrams key (DesignArtifacts).
    c4 = design.get("c4_diagram_url") or design.get("c4_diagrams") or ""
    api = design.get("api_contracts") or design.get("api_contract") or ""
    ordered = [
        ("hld", design.get("hld", "")),
        ("lld", design.get("lld", "")),
        ("api_contracts", api),
        ("database_schema", design.get("database_schema", "")),
        ("c4_diagram_url", c4),
        ("tech_stack", design.get("tech_stack", "")),
        ("adrs", design.get("adrs", "")),
        ("security_checklist", design.get("security_checklist", "")),
    ]
    for key, val in ordered:
        if val:
            lines.append(f"  {key.upper()}:\n{str(val)[:800]}")
    return "\n".join(lines)


def _fmt_development(dev: Dict[str, Any]) -> str:
    lines = ["[DEVELOPMENT ARTIFACTS]"]
    for key in ("summary", "files_changed", "pr_url", "test_instructions"):
        val = dev.get(key, "")
        if val:
            lines.append(f"  {key.upper()}: {str(val)[:400]}")
    return "\n".join(lines)


def _fmt_testing(testing: Dict[str, Any]) -> str:
    """Format the TestingArtifact JSON for the deployment agent.

    Schema: see agentic_app/shared/models/testing.py — TestingArtifact.

    Phase 11.2 — extended to render every field the testing agent's fan-out
    populates: functional_results, defect_log, security_findings, pr_coverage,
    qa_report_*_path. Each section renders only when its data is present, so
    legacy testing artifacts (without these fields) format identically to the
    pre-Phase-11 output.
    """
    lines = ["[TESTING ARTIFACTS]"]
    status = testing.get("status", "unknown")
    lines.append(f"  STATUS: {status}")

    lang = testing.get("language")
    if lang and lang != "unknown":
        lines.append(f"  LANGUAGE: {lang}")

    plan_count = testing.get("plan_test_case_count")
    if plan_count is not None:
        lines.append(f"  TEST CASES PLANNED: {plan_count}")

    te = testing.get("test_execution") or {}
    if te:
        lines.append(
            f"  EXECUTION: {te.get('total', 0)} total / "
            f"{te.get('passed', 0)} passed / "
            f"{te.get('failed', 0)} failed / "
            f"{te.get('errors', 0)} errors / "
            f"{te.get('skipped', 0)} skipped "
            f"({te.get('duration_ms', 0)}ms)"
        )

    cov = testing.get("coverage") or {}
    if cov:
        cov_pct = cov.get("coverage_pct")
        branch = cov.get("branch_coverage_pct")
        lines.append(
            f"  COVERAGE: {cov_pct}% line"
            + (f" / {branch}% branch" if branch is not None else "")
            + f" ({cov.get('statements', 0)} statements, {cov.get('missed', 0)} missed)"
        )

    pr_cov = testing.get("pr_coverage") or {}
    if pr_cov:
        lines.append(
            f"  PR COVERAGE: {pr_cov.get('coverage_pct', 0)}% changed lines covered "
            f"({pr_cov.get('changed_lines_covered', 0)}/{pr_cov.get('changed_lines_total', 0)} "
            f"across {pr_cov.get('files_changed', 0)} files vs {pr_cov.get('base_branch', 'main')})"
        )

    # Functional API scenario results (Phase 10)
    func_results = testing.get("functional_results") or []
    if func_results:
        passed = sum(1 for r in func_results if r.get("passed"))
        lines.append(
            f"  FUNCTIONAL API: {passed}/{len(func_results)} scenarios passed"
        )

    # Defect log (cross-skill aggregated)
    defects = testing.get("defect_log") or []
    if defects:
        critical = sum(1 for d in defects if d.get("severity") == "critical")
        high = sum(1 for d in defects if d.get("severity") == "high")
        medium = sum(1 for d in defects if d.get("severity") == "medium")
        low = sum(1 for d in defects if d.get("severity") == "low")
        lines.append(
            f"  DEFECT LOG: {len(defects)} total — "
            f"{critical} critical / {high} high / {medium} medium / {low} low"
        )

    # Security findings (Trivy/Sonar/Bandit/Roslyn)
    security = testing.get("security_findings") or []
    if security:
        sev_high = sum(1 for s in security if (s.get("severity") or "").upper() in ("CRITICAL", "HIGH"))
        lines.append(
            f"  SECURITY FINDINGS: {len(security)} total ({sev_high} critical/high)"
        )

    # Pipeline run record (when the testing agent triggered an ADO pipeline)
    pipe = testing.get("pipeline_run") or {}
    if pipe:
        lines.append(
            f"  PIPELINE RUN: #{pipe.get('run_id', '?')} {pipe.get('state', '?')} / "
            f"{pipe.get('result', '?')} ({pipe.get('project', '?')})"
        )

    summary = testing.get("summary_md")
    if summary:
        lines.append(f"  SUMMARY: {str(summary)[:600]}")

    files = testing.get("artifact_files") or []
    if files:
        lines.append(f"  ARTIFACT FILES: {', '.join(files[:10])}")

    qa_html = testing.get("qa_report_html_path")
    qa_pdf = testing.get("qa_report_pdf_path")
    if qa_html or qa_pdf:
        avail = []
        if qa_html:
            avail.append("HTML")
        if qa_pdf:
            avail.append("PDF")
        lines.append(f"  QA REPORT: available as {' + '.join(avail)}")

    return "\n".join(lines)


def _fmt_code_review(cr: Dict[str, Any]) -> str:
    lines = ["[CODE REVIEW ARTIFACTS]"]
    rec = cr.get("merge_recommendation", "unknown")
    lines.append(f"  MERGE RECOMMENDATION: {rec}")
    findings = cr.get("findings") or []
    if findings:
        high = sum(1 for f in findings if f.get("severity") in ("high", "critical"))
        medium = sum(1 for f in findings if f.get("severity") == "medium")
        low = sum(1 for f in findings if f.get("severity") == "low")
        lines.append(f"  FINDINGS: {len(findings)} total — {high} high/critical, {medium} medium, {low} low")
    summary = cr.get("review_summary")
    if summary:
        lines.append(f"  SUMMARY: {str(summary)[:600]}")
    return "\n".join(lines)


def _fmt_security(sec: Dict[str, Any]) -> str:
    lines = ["[SECURITY ARTIFACTS]"]
    risk = sec.get("risk_score", "unknown")
    lines.append(f"  RISK SCORE: {risk}")
    sign_off = sec.get("security_sign_off", False)
    lines.append(f"  SECURITY SIGN-OFF: {'yes' if sign_off else 'no'}")

    deps = sec.get("dependency_findings") or []
    if deps:
        critical_high = sum(1 for d in deps if d.get("severity") in ("critical", "high"))
        lines.append(f"  DEPENDENCY FINDINGS: {len(deps)} total ({critical_high} critical/high)")

    code = sec.get("code_findings") or []
    if code:
        lines.append(f"  CODE FINDINGS: {len(code)} total")

    secrets = sec.get("secret_findings") or []
    if secrets:
        lines.append(f"  SECRET FINDINGS: {len(secrets)} detected")

    summary = sec.get("scan_summary")
    if summary:
        lines.append(f"  SUMMARY: {str(summary)[:600]}")
    return "\n".join(lines)


_ARTIFACT_FORMATTERS = {
    "requirements_payload": _fmt_requirements,
    "design_artifacts": _fmt_design,
    "development_artifacts": _fmt_development,
    "testing_artifacts": _fmt_testing,
    "code_review_artifacts": _fmt_code_review,
    "security_artifacts": _fmt_security,
}


# ── Public API ────────────────────────────────────────────────────────────────

async def build_context(session_id: str, agent_id: str) -> str:
    """Return a formatted context string for the given agent, or '' if nothing available."""
    agent_def = AGENT_REGISTRY.get(agent_id)
    if not agent_def or not agent_def.input_artifacts:
        return ""

    try:
        artifacts = await fetch_session_artifacts(session_id)
    except Exception:
        return ""

    if not artifacts:
        return ""

    parts: list[str] = []
    for field_name in agent_def.input_artifacts:
        value = artifacts.get(field_name)
        if not value or not isinstance(value, dict):
            continue
        formatter = _ARTIFACT_FORMATTERS.get(field_name)
        if formatter:
            parts.append(formatter(value))

    return "\n\n".join(parts) if parts else ""


async def _fetch_artifacts_for_project(project_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    """The project's most recent Run row's artifact columns, or None if the project
    has no runs yet. `Run`, not `AgentSession`, is canonical for project-scoped
    upstream reads — matches Documentation's read_upstream_artifacts precedent
    (help/portfolio-1-agent-status.md's Documentation section)."""
    import uuid as _uuid

    from sqlalchemy import select

    from shared.db import get_db_session_for_tenant
    from shared.models.orm import Run

    async with get_db_session_for_tenant(tenant_id) as db:
        stmt = (
            select(Run)
            .where(Run.project_id == _uuid.UUID(project_id), Run.tenant_id == _uuid.UUID(tenant_id))
            .order_by(Run.created_at.desc())
            .limit(1)
        )
        run = (await db.execute(stmt)).scalars().first()
        if run is None:
            return None
        return {
            "requirements_payload": run.requirements_payload,
            "design_artifacts": run.design_artifacts,
            "development_artifacts": run.development_artifacts,
            "testing_artifacts": run.testing_artifacts,
            "code_review_artifacts": run.code_review_artifacts,
            "security_artifacts": run.security_artifacts,
        }


async def build_context_for_project(project_id: str, tenant_id: str, agent_id: str) -> str:
    """Same formatting as build_context, but resolved by PROJECT (the project's most
    recent Run row), not by session id. A fresh standalone-page conversation mints a
    brand-new session id unrelated to whatever session Requirements/Design used for
    theirs, so build_context's session-keyed lookup finds nothing even when the
    project's Requirements and Design have both been baselined. See
    docs/superpowers/specs/2026-08-31-development-agent-verification-design.md Part 4.3.
    """
    agent_def = AGENT_REGISTRY.get(agent_id)
    if not agent_def or not agent_def.input_artifacts or not project_id or not tenant_id:
        return ""
    try:
        artifacts = await _fetch_artifacts_for_project(project_id, tenant_id)
    except Exception:
        return ""
    if not artifacts:
        return ""
    parts: list[str] = []
    for field_name in agent_def.input_artifacts:
        value = artifacts.get(field_name)
        if not value or not isinstance(value, dict):
            continue
        formatter = _ARTIFACT_FORMATTERS.get(field_name)
        if formatter:
            parts.append(formatter(value))
    return "\n\n".join(parts) if parts else ""
