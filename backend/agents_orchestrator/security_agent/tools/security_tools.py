"""Native tools for the Security agent (read-only on the repo).

The branch under scan is cloned by the API and bound to the session; these tools
let the agent run layered scanners on the clone, read code for context, generate an
SBOM, read the upstream threat model, and submit the final structured artifact.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib

from langchain_core.tools import tool

from agents_orchestrator.security_agent.config.session_state import get_session
from agents_orchestrator.security_agent.tools.trivy_tool import run_trivy_scan
from agents_orchestrator.security_agent.tools.semgrep_sast_tool import run_semgrep_sast
from agents_orchestrator.security_agent.tools.gitleaks_tool import run_gitleaks_scan
from config.connection_manager import manager
from config.ws_helper import broadcast_log, get_session_id

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "bin", "obj", "dist", "build"}
_MAX_FILE_BYTES = 200_000
_MANIFESTS = {
    "package.json", "requirements.txt", "pyproject.toml", "go.mod", "pom.xml",
    "build.gradle", "Gemfile", "composer.json", "Cargo.toml", "packages.config",
}


def _is_manifest(fn: str) -> bool:
    return fn in _MANIFESTS or fn.endswith(".csproj")


def _work_dir() -> pathlib.Path | None:
    s = get_session(get_session_id())
    return pathlib.Path(s.work_dir) if s.work_dir else None


@tool
async def scan_dependencies() -> str:
    """Run a dependency / vulnerability (SCA) scan on the checked-out repo (Trivy).

    Degrades gracefully if the scanner binary is unavailable. Returns scanner JSON.
    """
    wd = _work_dir()
    if wd is None or not wd.exists():
        return "ERROR: no scan workspace prepared. Ask the user to select a branch or PR first."
    result_json = await asyncio.to_thread(run_trivy_scan.invoke, {"target_path": str(wd)})
    try:
        parsed = json.loads(result_json)
        if parsed.get("status") == "ok":
            get_session(get_session_id()).last_trivy_findings = parsed.get("findings", [])
    except (json.JSONDecodeError, AttributeError):
        pass
    return result_json


@tool
async def scan_code() -> str:
    """Run a static application security (SAST, OWASP) scan on the checked-out repo (Semgrep)."""
    wd = _work_dir()
    if wd is None or not wd.exists():
        return "ERROR: no scan workspace prepared."
    return await asyncio.to_thread(run_semgrep_sast.invoke, {"target_path": str(wd)})


@tool
async def scan_secrets() -> str:
    """Scan the checked-out repo for hardcoded secrets / credentials (Gitleaks)."""
    wd = _work_dir()
    if wd is None or not wd.exists():
        return "ERROR: no scan workspace prepared."
    return await asyncio.to_thread(run_gitleaks_scan.invoke, {"target_path": str(wd)})


@tool
async def generate_sbom(max_components: int = 200) -> str:
    """Build a lightweight SBOM by parsing dependency manifests in the repo.

    Returns JSON {components:[{name, version, manifest, vulnerabilities}], manifests:[...],
    vulnerability_data:"trivy"|"not_scanned_yet"}.
    `vulnerabilities` is populated from the same session's scan_dependencies (Trivy) run.
    If scan_dependencies has NOT run this session, every component's `vulnerabilities` is
    null (not 0) and the top-level `vulnerability_data` is "not_scanned_yet" -- a count is
    never fabricated, and a real scanned "0 matches" is never confused with "unknown".
    """
    wd = _work_dir()
    if wd is None or not wd.exists():
        return "ERROR: no scan workspace prepared."
    comps: list[dict] = []
    seen_manifests: list[str] = []
    for dirpath, dirs, files in os.walk(wd):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            if not _is_manifest(fn):
                continue
            fp = pathlib.Path(dirpath) / fn
            rel = str(fp.relative_to(wd)).replace("\\", "/")
            seen_manifests.append(rel)
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")[:_MAX_FILE_BYTES]
            except Exception:
                continue
            comps.extend(_parse_manifest(fn, text, rel))
            if len(comps) >= max_components:
                break

    trivy_findings = get_session(get_session_id()).last_trivy_findings
    for comp in comps:
        if trivy_findings is None:
            comp["vulnerabilities"] = None
        else:
            comp["vulnerabilities"] = sum(
                1 for f in trivy_findings
                if f.get("package", "").lower() == comp["name"].lower()
                and comp["version"] and f.get("installed_version", "") == comp["version"]
            )

    return json.dumps({
        "components": comps[:max_components],
        "manifests": seen_manifests,
        "vulnerability_data": "trivy" if trivy_findings is not None else "not_scanned_yet",
    })


def _parse_manifest(fn: str, text: str, rel: str) -> list[dict]:
    import re

    out: list[dict] = []
    try:
        if fn.endswith(".csproj"):
            # <PackageReference Include="X" Version="Y" /> (Version attr or child element)
            for m in re.finditer(r'<PackageReference\s+Include="([^"]+)"(?:[^>]*?Version="([^"]+)")?', text):
                out.append({"name": m.group(1), "version": m.group(2) or "", "manifest": rel})
        elif fn == "packages.config":
            for m in re.finditer(r'<package\s+id="([^"]+)"\s+version="([^"]+)"', text):
                out.append({"name": m.group(1), "version": m.group(2), "manifest": rel})
        elif fn in ("package.json", "composer.json"):
            data = json.loads(text)
            for sect in ("dependencies", "devDependencies", "require", "require-dev"):
                for name, ver in (data.get(sect) or {}).items():
                    out.append({"name": name, "version": str(ver), "manifest": rel})
        elif fn == "requirements.txt":
            for ln in text.splitlines():
                ln = ln.strip()
                if not ln or ln.startswith("#"):
                    continue
                for sep in ("==", ">=", "<=", "~=", ">", "<"):
                    if sep in ln:
                        n, v = ln.split(sep, 1)
                        out.append({"name": n.strip(), "version": v.strip(), "manifest": rel})
                        break
                else:
                    out.append({"name": ln, "version": "", "manifest": rel})
        elif fn == "go.mod":
            for ln in text.splitlines():
                ln = ln.strip()
                if ln.startswith("require ") or (ln and ln[0].islower() and "/" in ln and " v" in ln):
                    parts = ln.replace("require ", "").split()
                    if len(parts) >= 2:
                        out.append({"name": parts[0], "version": parts[1], "manifest": rel})
    except Exception:
        pass
    return out


@tool
async def read_repo_file(path: str) -> str:
    """Read one repo-relative file from the cloned repo for security context.

    Args:
        path: repo-relative file path.
    """
    root = _work_dir()
    if root is None or not root.exists():
        return "ERROR: no scan workspace prepared."
    try:
        target = (root / path).resolve()
        target.relative_to(root.resolve())
    except ValueError:
        return "ERROR: path traversal denied."
    if not target.exists() or not target.is_file():
        return f"ERROR: file not found: {path}"
    return target.read_bytes()[:_MAX_FILE_BYTES].decode("utf-8", errors="replace")


@tool
async def search_repo(query: str, max_results: int = 40) -> str:
    """Grep the cloned repo for a literal string (trace data flow / find sinks & sources)."""
    root = _work_dir()
    if root is None or not root.exists():
        return "ERROR: no scan workspace prepared."
    hits: list[dict] = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            if len(hits) >= max_results:
                break
            fp = pathlib.Path(dirpath) / fn
            try:
                if fp.stat().st_size > _MAX_FILE_BYTES:
                    continue
                for i, line in enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if query in line:
                        rel = str(fp.relative_to(root)).replace("\\", "/")
                        hits.append({"file": rel, "line": i, "text": line.strip()[:200]})
                        if len(hits) >= max_results:
                            break
            except Exception:
                continue
    return json.dumps({"matches": hits, "count": len(hits)})


async def _latest_design_artifact(tenant_id: str, project_id: str) -> dict | None:
    """Standalone read: this project's latest `design_artifacts` row (unchanged path).

    This is the byte-for-byte-preserved behaviour the live standalone Security page
    relies on — the project-latest Design row, ordered newest-first.
    """
    import uuid
    from sqlalchemy import select
    from shared.db import get_db_session_for_tenant
    from shared.models.orm import Run

    if not tenant_id or not project_id:
        return None
    try:
        async with get_db_session_for_tenant(tenant_id) as db:
            row = (
                await db.execute(
                    select(Run.design_artifacts)
                    .where(Run.project_id == uuid.UUID(project_id), Run.design_artifacts.isnot(None))
                    .order_by(Run.created_at.desc())
                    .limit(1)
                )
            ).scalars().first()
            return row
    except Exception:
        return None


async def _run_design_artifact(tenant_id: str, run_id: str) -> dict | None:
    """Pipeline read: `design_artifacts` off the CURRENT run by id.

    Unlike `_latest_design_artifact` (project-latest, for the standalone page), this
    reads the exact `runs` row the pipeline is executing so Security sees THIS run's
    upstream Design — not a stale sibling run.
    """
    import uuid
    from sqlalchemy import select
    from shared.db import get_db_session_for_tenant
    from shared.models.orm import Run

    if not tenant_id or not run_id:
        return None
    try:
        async with get_db_session_for_tenant(tenant_id) as db:
            row = (
                await db.execute(
                    select(Run.design_artifacts).where(Run.id == uuid.UUID(str(run_id)))
                )
            ).scalars().first()
            return row
    except Exception:
        return None


async def _run_exists(tenant_id: str, run_id: str) -> bool:
    """True iff a `runs` row with this id exists — the pipeline-mode discriminator.

    A standalone chat session id is a client-generated UUID with no `runs` row; a
    pipeline session id IS the run_id. Any DB/parse failure returns False so we fall
    through to the unchanged standalone path.
    """
    import uuid

    if not tenant_id or not run_id:
        return False
    try:
        _uid = uuid.UUID(str(run_id))
    except (ValueError, TypeError, AttributeError):
        return False
    try:
        from sqlalchemy import select
        from shared.db import get_db_session_for_tenant
        from shared.models.orm import Run

        async with get_db_session_for_tenant(tenant_id) as db:
            hit = (await db.execute(select(Run.id).where(Run.id == _uid))).scalars().first()
            return hit is not None
    except Exception:
        return False


@tool
async def read_design_artifacts() -> str:
    """Read this project's Design artifacts (threat model / security checklist), if any.

    Dual-mode: in pipeline mode (session id is a real `run_id`) reads the CURRENT run;
    otherwise falls through to the UNCHANGED standalone project-latest read so the live
    Security page behaves byte-for-byte as before.
    """
    s = get_session(get_session_id())
    if not s.tenant_id:
        return "No design artifact available."
    sid = get_session_id()
    if await _run_exists(s.tenant_id, sid):
        row = await _run_design_artifact(s.tenant_id, sid)
    else:
        row = await _latest_design_artifact(s.tenant_id, s.project_id)
    if not row:
        return "No design artifact found for this project (scan without a threat-model checklist)."
    return json.dumps(row)[:12000]


@tool
async def submit_security_review(review_json: str) -> str:
    """Submit the final structured security review. Call this ONCE when analysis is done.

    Args:
        review_json: a JSON object with keys:
          summary (markdown), risk_score ("critical"|"high"|"medium"|"low"|"none"),
          signoff: {decision: "pass"|"fail"|"conditional", rationale},
          findings: [{id, severity, category (sca|sast|secret|iac|container|license|supply_chain),
                      title, cve?, file, line, package?, reachability, triage, description,
                      remediation, autofix_patch?, compliance:[...]}],
          sbom: [{name, version, license?, vulnerabilities?}],
          supply_chain: [{package, risk, note}],
          remediation_plan (markdown),
          suppression_log: [{finding_id, reason}],
          compliance_frameworks: [...]
    """
    from shared.models.security import SecurityArtifact, ScanContext, SecurityMetrics, Signoff

    s = get_session(get_session_id())
    try:
        payload = json.loads(review_json) if isinstance(review_json, str) else dict(review_json)
    except Exception as exc:
        return f"ERROR: review_json was not valid JSON ({exc}). Re-send a single JSON object."

    ctx = ScanContext(
        repo_name=s.repo_name, ado_project=s.ado_project, mode=s.mode or "branch",
        branch=s.branch, pr_id=s.pr_id or None, pr_title=s.pr_title or None, head_sha=s.head_sha,
    )
    findings = payload.get("findings", []) or []
    sev = lambda k: sum(1 for f in findings if f.get("severity") == k)  # noqa: E731
    metrics = SecurityMetrics(
        critical=sev("critical"), high=sev("high"), medium=sev("medium"),
        low=sev("low"), total=len(findings),
    )
    sign = payload.get("signoff") or {}
    try:
        artifact = SecurityArtifact(
            context=ctx,
            summary=payload.get("summary", ""),
            risk_score=payload.get("risk_score", "none"),
            signoff=Signoff(decision=sign.get("decision", "conditional"), rationale=sign.get("rationale", "")),
            findings=findings,
            sbom=payload.get("sbom", []),
            supply_chain=payload.get("supply_chain", []),
            remediation_plan=payload.get("remediation_plan", ""),
            suppression_log=payload.get("suppression_log", []),
            compliance_frameworks=payload.get("compliance_frameworks") or ["OWASP Top 10"],
            metrics=metrics,
            status="scanned",
        )
    except Exception as exc:
        return f"ERROR: review did not match the required shape: {exc}"

    s.last_artifact = artifact.model_dump()
    broadcast_log(
        manager,
        f"Security scan complete: {metrics.total} findings ({metrics.critical} critical) "
        f"→ risk={artifact.risk_score}, signoff={artifact.signoff.decision}",
        level="INFO",
    )
    return (
        f"Security review submitted: {metrics.total} findings, risk={artifact.risk_score}, "
        f"signoff={artifact.signoff.decision}. It will be saved and shown in the tabs."
    )
