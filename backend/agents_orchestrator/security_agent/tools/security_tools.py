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
import re

from langchain_core.tools import tool

from agents_orchestrator.security_agent.config.session_state import get_session
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


async def _full_scan() -> dict | str:
    """The one scan of this target — every scanner, once — or an error string.

    THE SECURITY AGENT PASSED A BRANCH WITH NINE HIGH CVEs. Its `scan_dependencies` ran
    Trivy on the raw checkout, and the branch commits no lockfile, so Trivy had nothing to
    look at and reported nothing; `generate_sbom` parsed the manifests alone, so the
    transitive `tar` that carries the CVEs was never listed. The same commit, reviewed by
    the Code Review agent minutes earlier, showed 13 vulnerabilities — its scan resolves
    the declared ranges in a scratch copy first (shared/services/code_security_scan.py)
    and records each scanner's status. The four tools below are slices of THAT scan, run
    once per prepared target and kept on the session.
    """
    from shared.services.code_security_scan import run_code_security_scan  # noqa: PLC0415

    wd = _work_dir()
    if wd is None or not wd.exists():
        return "ERROR: no scan workspace prepared. Ask the user to select a branch or PR first."
    s = get_session(get_session_id())
    cached = getattr(s, "security_scan", None)
    if cached and cached.get("_work_dir") == str(wd):
        return cached
    result = await asyncio.to_thread(
        run_code_security_scan, str(wd),
        progress=lambda m: broadcast_log(manager, m, level="INFO"),
    )
    result["_work_dir"] = str(wd)
    s.security_scan = result
    # `generate_sbom` and the report count vulnerabilities per component from this.
    trivy_ok = any(sc.get("name") == "Trivy" and sc.get("status") == "ok" for sc in result.get("scanners", []))
    s.last_trivy_findings = [
        {"cve": v.get("id", ""), "severity": v.get("severity", ""), "package": v.get("package", ""),
         "installed_version": v.get("installed", ""), "fixed_version": v.get("fixed", ""),
         "title": v.get("title", ""), "target": v.get("manifest", "")}
        for v in result.get("vulnerabilities", [])
    ] if trivy_ok else None
    return result


#: Refusals of one review in one turn before submit stops taking attempts. A live run
#: sent the same (correct) review eleven times to a gate that could not match it, and
#: the turn ran into the recursion limit ten minutes later with nothing said.
_MAX_REFUSALS = 3

# `tar@6.2.1` → `tar`; `@types/node@20.1.0` → `@types/node` (the leading @ is the scope).
_VERSION_SUFFIX = re.compile(r"(?<=.)@[^@/]*$")


def _package_key(name: object) -> str:
    """The bare package name, whichever way the model wrote it.

    A live run recorded the package as `tar@6.2.1`; the gate compared it with the
    scanner's `tar` and refused a review that addressed it."""
    n = re.split(r"[\s:=,]", str(name or "").strip().lower(), maxsplit=1)[0]
    return _VERSION_SUFFIX.sub("", n)


def _tokens(value: object) -> set[str]:
    """CVE / GHSA / finding ids from a string ("CVE-A, CVE-B") or a list of them."""
    items = value if isinstance(value, (list, tuple, set)) else [value]
    return {t.lower() for item in items for t in re.split(r"[\s,;]+", str(item or "")) if t}


def _unaddressed_scan_results(scan: dict, payload: dict) -> str | None:
    """The refusal for a review that leaves out what the scan found — or None.

    A package is addressed by a finding naming it (in any version spelling) or any of
    its CVE ids, or by a suppression whose finding_id is the package, one of its CVE
    ids, or the id of a finding that names it."""
    findings_in = [f for f in (payload.get("findings") or []) if isinstance(f, dict)]
    suppressions = [e for e in (payload.get("suppression_log") or []) if isinstance(e, dict)]
    packages = {_package_key(f.get("package")) for f in findings_in} - {""}
    ids: set[str] = set()
    for f in findings_in:
        ids |= _tokens(f.get("cve")) | _tokens(f.get("id"))
    for e in suppressions:
        ref = _tokens(e.get("finding_id"))
        ids |= ref
        packages |= {_package_key(r) for r in ref}

    unaddressed = sorted({
        f"{v.get('package')} {v.get('installed')}"
        for v in scan.get("vulnerabilities") or []
        if (v.get("severity") or "").lower() in ("critical", "high")
        and _package_key(v.get("package")) not in packages
        and str(v.get("id") or "").lower() not in ids
    })
    if unaddressed:
        return (
            "ERROR: the scan found high/critical vulnerabilities that this review does not "
            f"address: {', '.join(unaddressed)}. Record each package as ONE finding (category "
            "sca, severity = the worst CVE, package = the package name, cve = the CVE ids, "
            "reachability, triage — 'acceptable_risk' or 'false_positive' if you judge it "
            "unreachable, with the reason in `description`) or add it to suppression_log with "
            "finding_id = the package name or CVE id and a reason, then submit again."
        )
    if (scan.get("secrets") or []) and not any((f.get("category") or "") == "secret" for f in findings_in):
        return (
            f"ERROR: the scan found {len(scan['secrets'])} hardcoded secret(s) and this review "
            "records none. Add a finding (category secret) per secret, then submit again."
        )
    return None


def _refuse(s, refusal: str) -> str:
    s.submit_refusals += 1
    s.last_submit_refusal = refusal
    if s.submit_refusals >= _MAX_REFUSALS:
        return (
            f"{refusal} This was refusal {s.submit_refusals} of {_MAX_REFUSALS}: do not call "
            "submit_security_review again this turn. Tell the user plainly that the security "
            "review was NOT saved, and why."
        )
    return refusal


def _scanner(result: dict, name: str) -> dict:
    return next((sc for sc in result.get("scanners", []) if sc.get("name") == name), {"status": "error", "message": f"{name} did not run"})


@tool
async def scan_dependencies() -> str:
    """Run the dependency / vulnerability (SCA) scan on the checked-out repo (Trivy), with
    the declared dependency ranges resolved first so a repo that commits no lockfile is
    still checked. Returns JSON {status, findings_count, findings:[{cve, severity, package,
    installed_version, fixed_version, title, target}], message}. A status other than "ok"
    means the scanner did NOT run — never treat that as clean.
    """
    result = await _full_scan()
    if isinstance(result, str):
        return result
    sc = _scanner(result, "Trivy")
    findings = get_session(get_session_id()).last_trivy_findings or []
    return json.dumps({
        "status": sc.get("status"), "message": sc.get("message", ""),
        "findings_count": len(findings) if sc.get("status") == "ok" else None,
        "findings": findings if sc.get("status") == "ok" else [],
        "notes": (result.get("sbom") or {}).get("notes", []),
    })


@tool
async def scan_code() -> str:
    """Run the static application security (SAST, OWASP Top 10) scan on the checked-out
    repo (Semgrep). Returns JSON {status, findings_count, findings, message}; a status
    other than "ok" means it did NOT run."""
    result = await _full_scan()
    if isinstance(result, str):
        return result
    sc = _scanner(result, "Semgrep")
    findings = result.get("sast") or []
    return json.dumps({"status": sc.get("status"), "message": sc.get("message", ""),
                       "findings_count": len(findings) if sc.get("status") == "ok" else None,
                       "findings": findings if sc.get("status") == "ok" else []})


@tool
async def scan_secrets() -> str:
    """Scan the checked-out repo for hardcoded secrets / credentials (Gitleaks). Returns
    JSON {status, findings_count, findings, message}; a status other than "ok" means it
    did NOT run."""
    result = await _full_scan()
    if isinstance(result, str):
        return result
    sc = _scanner(result, "Gitleaks")
    findings = result.get("secrets") or []
    return json.dumps({"status": sc.get("status"), "message": sc.get("message", ""),
                       "findings_count": len(findings) if sc.get("status") == "ok" else None,
                       "findings": findings if sc.get("status") == "ok" else []})


@tool
async def generate_sbom(max_components: int = 200) -> str:
    """The software bill of materials for the checked-out repo: every declared AND
    transitive component (dependency ranges are resolved first), each with its version,
    licence, the direct dependency that brings it in (`via`) and its vulnerability count.

    Returns JSON {components:[{name, version, license, direct, via, manifest,
    vulnerabilities}], manifests:[...], notes:[...], vulnerability_data:"trivy"|"not_scanned"}.
    `vulnerabilities` is null (not 0) when the vulnerability scanner did not run — a
    count is never fabricated. Components with vulnerabilities are listed first.
    """
    result = await _full_scan()
    if isinstance(result, str):
        return result
    sbom = result.get("sbom") or {}
    comps = sorted(sbom.get("components") or [],
                   key=lambda c: (-(c.get("vulnerabilities") or 0), not c.get("direct"), c.get("name") or ""))
    trivy_ok = _scanner(result, "Trivy").get("status") == "ok"
    return json.dumps({
        "components": [
            {"name": c.get("name"), "version": c.get("version"), "license": c.get("license"),
             "direct": c.get("direct"), "via": c.get("via"), "manifest": c.get("manifest"),
             "vulnerabilities": c.get("vulnerabilities") if trivy_ok else None}
            for c in comps[:max_components]
        ],
        "total_components": len(comps),
        "manifests": sbom.get("manifests", []),
        "notes": sbom.get("notes", []),
        "vulnerability_data": "trivy" if trivy_ok else "not_scanned",
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

    from shared.services.artifact_consumption import describe, read_upstream_for_agent

    async def _legacy():
        """The dual-mode read, unchanged. Pipeline mode reads THIS run's row rather
        than the project's latest, and that distinction has to survive for projects
        that have not opted in to enforcement."""
        if await _run_exists(s.tenant_id, sid):
            return await _run_design_artifact(s.tenant_id, sid)
        return await _latest_design_artifact(s.tenant_id, s.project_id)

    result = await read_upstream_for_agent(
        tenant_id=s.tenant_id, project_id=s.project_id, stage="design",
        consumer_stage="security", legacy_reader=_legacy,
    )
    if not result.found:
        if result.unenforced:
            return "No design artifact found for this project (scan without a threat-model checklist)."
        return describe(result)
    return json.dumps(result.payload)[:12000]


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
    scan = getattr(s, "security_scan", None)
    if not scan:
        return (
            "ERROR: the scanners have not run for this target. Call scan_dependencies, "
            "scan_code and scan_secrets first — the report's scanner results and SBOM come "
            "from them, not from this payload."
        )
    try:
        payload = json.loads(review_json) if isinstance(review_json, str) else dict(review_json)
    except Exception as exc:
        return f"ERROR: review_json was not valid JSON ({exc}). Re-send a single JSON object."

    # SIGN-OFF IS GATED ON THE SCAN. A live run signed off "pass, risk none, 0 findings"
    # on a branch whose scan had 9 high/critical CVEs — the reviewer had judged them
    # unreachable and simply left them out. A judgement is fine; hiding what the scanner
    # found is not. Every package with a high or critical vulnerability must appear as
    # a finding (with its reachability and triage) or in the suppression log with a
    # reason; a hardcoded secret must appear as a finding.
    if s.submit_refusals >= _MAX_REFUSALS:
        return (
            f"ERROR: submit_security_review has refused this review {s.submit_refusals} times this "
            "turn and will not take another attempt. Do not call it again. Tell the user plainly "
            f"that the security review was NOT saved, and why: {s.last_submit_refusal}"
        )
    refusal = _unaddressed_scan_results(scan, payload)
    if refusal:
        return _refuse(s, refusal)

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
            # THE SBOM IS THE SCANNER'S, NOT THE MODEL'S. The payload's `sbom` is whatever
            # the model re-typed — four components of 475, in a live run.
            sbom=[
                {"name": c.get("name", ""), "version": c.get("version", ""), "license": c.get("license"),
                 "vulnerabilities": c.get("vulnerabilities") or 0}
                for c in (scan.get("sbom") or {}).get("components") or []
            ],
            supply_chain=payload.get("supply_chain", []),
            remediation_plan=payload.get("remediation_plan", ""),
            suppression_log=payload.get("suppression_log", []),
            compliance_frameworks=payload.get("compliance_frameworks") or ["OWASP Top 10"],
            metrics=metrics,
            status="scanned",
            scan={k: v for k, v in scan.items() if not k.startswith("_")},
        )
    except Exception as exc:
        return _refuse(s, f"ERROR: review did not match the required shape: {exc}")

    s.last_artifact = artifact.model_dump()
    s.submit_refusals, s.last_submit_refusal = 0, ""
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
