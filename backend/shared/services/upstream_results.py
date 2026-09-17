"""Upstream stage results as another agent should read them: about THIS repository, and
small enough to be read whole.

TWO LIVE FAILURES, one cause each.

A handover described the wrong system. The project's newest code review was of another
repository reviewed minutes earlier (a .NET app); the Documentation agent was handed it as
"the code review" and wrote that the QuickLink branch "implements RadAuthPortal .NET".
`other_repo` says when a result is about a different repository.

A readiness report said "zero high vulns". The security result now carries the scanners'
own output and the SBOM — 168 KB for a small Node service — and the tools cut their JSON at
12–20 KB, so an agent read a slice of SBOM rows showing 0 vulnerabilities each. `compact`
keeps the verdict, the findings and the scan's totals and vulnerable packages, and drops
the long lists nobody reads line by line.
"""
from __future__ import annotations

from typing import Any


def other_repo(payload: Any, repo_name: str) -> str | None:
    """A description of the repository `payload` is about, when that is NOT `repo_name`."""
    if not isinstance(payload, dict) or not repo_name:
        return None
    ctx = _dict(payload.get("context"))
    theirs = str(ctx.get("repo_name") or "").strip()
    if not theirs or theirs.lower() == repo_name.strip().lower():
        return None
    branch = ctx.get("source_branch") or ctx.get("branch") or ""
    sha = str(ctx.get("head_sha") or "")[:7]
    return f"repository '{theirs}'" + (f", branch {branch}" if branch else "") + (f", commit {sha}" if sha else "")


def _dicts(value: Any) -> list[dict]:
    """The dict items of a list — a stored result is not guaranteed to be well formed."""
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _finding(f: dict) -> dict:
    keep = ("id", "severity", "category", "title", "file", "line", "package", "cve", "reachability", "triage", "description")
    out = {k: f.get(k) for k in keep if f.get(k) not in (None, "", [])}
    if isinstance(out.get("description"), str) and len(out["description"]) > 300:
        out["description"] = out["description"][:300] + "…"
    return out


def _vulnerable_packages(scan: dict) -> list[dict]:
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    groups: dict[str, dict] = {}
    for v in _dicts(scan.get("vulnerabilities")):
        key = f"{v.get('package')}@{v.get('installed')}"
        g = groups.setdefault(key, {"package": v.get("package"), "installed": v.get("installed"), "worst": "low", "count": 0, "high_or_critical": 0})
        sev = (v.get("severity") or "").lower()
        g["count"] += 1
        g["high_or_critical"] += sev in ("critical", "high")
        if rank.get(sev, 9) < rank.get(g["worst"], 9):
            g["worst"] = sev
    return sorted(groups.values(), key=lambda g: (rank.get(g["worst"], 9), -g["count"]))


def compact_security(payload: dict) -> dict:
    scan = _dict(payload.get("scan"))
    out = {
        "context": payload.get("context"),
        "signoff": payload.get("signoff"),
        "risk_score": payload.get("risk_score"),
        "metrics": payload.get("metrics"),
        "summary": str(payload.get("summary") or "")[:800],
        "findings": [_finding(f) for f in _dicts(payload.get("findings"))],
        "suppression_log": _dicts(payload.get("suppression_log")),
        "sbom_components": len(payload.get("sbom") or []) if isinstance(payload.get("sbom"), list) else 0,
        "report": {k: v for k, v in _dict(payload.get("document")).items() if k in ("filename", "artifact_id", "error")},
    }
    if scan:
        out["scan"] = {
            "scanners": [{k: sc.get(k) for k in ("name", "status", "findings")} for sc in _dicts(scan.get("scanners"))],
            "totals": scan.get("totals"),
            "vulnerable_packages": _vulnerable_packages(scan),
        }
        out["note"] = (
            "The scan's vulnerabilities are real findings even where the reviewer triaged them "
            "as acceptable risk — report them as found-and-accepted, never as a clean scan."
        )
    return out


def compact_code_review(payload: dict) -> dict:
    scope = _dict(payload.get("scope"))
    out = {k: payload.get(k) for k in (
        "context", "merge_recommendation", "summary", "security_summary",
        "requirements_coverage", "design_conformance",
    ) if payload.get(k) not in (None, "", [], {})}
    out["findings"] = [_finding(f) for f in _dicts(payload.get("findings"))]
    if scope:
        out["scope"] = {k: v for k, v in scope.items() if not isinstance(v, (list, dict))}
    if _dict(payload.get("document")):
        out["report"] = {k: v for k, v in payload["document"].items() if k in ("filename", "artifact_id", "error")}
    return out


def compact(stage: str, payload: Any) -> Any:
    """`payload` as another agent should read it; unchanged for stages without a digest."""
    if not isinstance(payload, dict):
        return payload
    if stage == "security":
        return compact_security(payload)
    if stage == "code_review":
        return compact_code_review(payload)
    return payload
