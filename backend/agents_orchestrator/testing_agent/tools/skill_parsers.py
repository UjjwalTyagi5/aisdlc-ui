"""Phase 11.4 — output parsers for shell-capable skills.

When a SKILL.md sets `runtime: shell`, dispatch_test_types runs the declared
shell_command via the sandbox, then pipes the produced report through the
parser named in `output_parser`. Each parser maps a tool's raw output (JSON
file on disk) to a typed pydantic model that fits an AggregatedResults field.

To register a new parser:
1. Write a function `parse_<tool_name>(report_path: str) -> dict | None`
   that returns a model_dump (dict) on success, None on missing/malformed.
2. Add to `_PARSERS` mapping below.
3. Reference its key in your SKILL.md's `output_parser` field.

All parsers are defensive: missing file / malformed JSON / unexpected schema
all return None (or a minimal sensible default), never raise.
"""
from __future__ import annotations

import json
import logging
import os
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional

from shared.models import (
    DependencyVulnerability,
    MutationResult,
    SecurityFinding,
)

logger = logging.getLogger("testing_agent.skill_parsers")


# --- Stryker.NET / Stryker-JS / mutmut → MutationResult ---------------------


def parse_stryker_json(report_path: str) -> Optional[Dict[str, Any]]:
    """Parse a Stryker reports/mutation/mutation-report.json (.NET or JS).

    Stryker emits a `mutation-report.json` with the schema:
        { thresholds, files: { <path>: { mutants: [{status, mutator,...}, ...] } } }

    `status` ∈ {Killed, Survived, NoCoverage, Timeout, RuntimeError, ...}.
    Kill rate = killed / (killed + survived) per Stryker's convention
    (excluding NoCoverage / RuntimeError / Timeout from the denominator).
    """
    if not os.path.exists(report_path):
        return None
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"parse_stryker_json: failed to read {report_path}: {exc}")
        return None

    files = data.get("files") or {}
    if not isinstance(files, dict):
        return None

    killed = survived = no_cov = timeout = total = 0
    survivors: List[Dict[str, Any]] = []
    for filename, file_obj in files.items():
        for mut in (file_obj or {}).get("mutants") or []:
            total += 1
            status = (mut.get("status") or "").lower()
            if status == "killed":
                killed += 1
            elif status == "survived":
                survived += 1
                if len(survivors) < 10:
                    survivors.append({
                        "file": filename,
                        "line": (mut.get("location") or {}).get("start", {}).get("line"),
                        "mutator": mut.get("mutatorName") or mut.get("mutator"),
                        "original": mut.get("originalCode") or "",
                        "mutated": mut.get("mutatedCode") or "",
                    })
            elif status == "nocoverage":
                no_cov += 1
            elif status == "timeout":
                timeout += 1

    denom = killed + survived
    kill_rate = (killed / denom * 100.0) if denom else 0.0

    return MutationResult(
        tool="stryker",
        kill_rate_pct=round(kill_rate, 2),
        mutants_total=total,
        mutants_killed=killed,
        mutants_survived=survived,
        mutants_timeout=timeout,
        mutants_no_coverage=no_cov,
        top_survivors=survivors[:5],
        report_path=report_path,
    ).model_dump()


def parse_mutmut_summary(report_path: str) -> Optional[Dict[str, Any]]:
    """Parse a mutmut JSON dump (Python). mutmut emits a summary like:
        {"killed": N, "survived": N, "timeout": N, "suspicious": N, ...}
    Less rich than Stryker — no per-mutant detail in the basic dump."""
    if not os.path.exists(report_path):
        return None
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"parse_mutmut_summary: failed: {exc}")
        return None

    killed = int(data.get("killed", 0))
    survived = int(data.get("survived", 0))
    timeout = int(data.get("timeout", 0))
    suspicious = int(data.get("suspicious", 0))
    total = killed + survived + timeout + suspicious
    denom = killed + survived
    kill_rate = (killed / denom * 100.0) if denom else 0.0

    return MutationResult(
        tool="mutmut",
        kill_rate_pct=round(kill_rate, 2),
        mutants_total=total,
        mutants_killed=killed,
        mutants_survived=survived,
        mutants_timeout=timeout,
        mutants_no_coverage=0,
        top_survivors=[],
        report_path=report_path,
    ).model_dump()


# --- Bandit → SecurityFinding[] --------------------------------------------


def parse_bandit_json(report_path: str) -> Optional[List[Dict[str, Any]]]:
    """Parse `bandit -f json -o <report>` output.

    Bandit emits {"results": [{"issue_severity", "issue_text", "test_id",
    "filename", "line_number", "issue_cwe": {"id": ...}}, ...]}.
    """
    if not os.path.exists(report_path):
        return None
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"parse_bandit_json: failed: {exc}")
        return None

    results = data.get("results") or []
    if not isinstance(results, list):
        return []

    out: List[Dict[str, Any]] = []
    for r in results:
        sev = (r.get("issue_severity") or "MEDIUM").upper()
        cwe_obj = r.get("issue_cwe") or {}
        cwe_id = cwe_obj.get("id") if isinstance(cwe_obj, dict) else None
        out.append(SecurityFinding(
            source="bandit",
            severity=sev,
            rule_id=str(r.get("test_id") or "B-?"),
            file=str(r.get("filename") or "?"),
            line=int(r.get("line_number") or 0) or None,
            message=str(r.get("issue_text") or ""),
            cwe=f"CWE-{cwe_id}" if cwe_id else None,
        ).model_dump())
    return out


# --- pip-audit / npm audit / dotnet list package --vulnerable → DependencyVulnerability[]


def parse_pip_audit_json(report_path: str) -> Optional[List[Dict[str, Any]]]:
    """Parse `pip-audit -f json -o <report>` output.
    Schema: {"dependencies": [{"name", "version", "vulns": [{"id", "fix_versions", ...}]}]}
    """
    if not os.path.exists(report_path):
        return None
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"parse_pip_audit_json: failed: {exc}")
        return None

    deps = data.get("dependencies") or []
    if not isinstance(deps, list):
        return []

    out: List[Dict[str, Any]] = []
    for d in deps:
        name = d.get("name") or "?"
        version = d.get("version")
        for v in d.get("vulns") or []:
            cve_id = v.get("id") or v.get("alias", [None])[0] or "?"
            out.append(DependencyVulnerability(
                source="pip-audit",
                package=name,
                installed_version=version,
                severity=(v.get("severity") or "UNKNOWN").upper(),
                cve=cve_id,
                advisory_url=v.get("link") or v.get("references", [None])[0],
                fix_versions=list(v.get("fix_versions") or []),
                summary=v.get("description") or v.get("summary"),
            ).model_dump())
    return out


def parse_npm_audit_json(report_path: str) -> Optional[List[Dict[str, Any]]]:
    """Parse `npm audit --json` output. Schema is npm v7+:
    {"vulnerabilities": {"<package>": {"severity", "via": [...], "range", ...}}}
    """
    if not os.path.exists(report_path):
        return None
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"parse_npm_audit_json: failed: {exc}")
        return None

    vulns = data.get("vulnerabilities") or {}
    if not isinstance(vulns, dict):
        return []

    out: List[Dict[str, Any]] = []
    for pkg_name, info in vulns.items():
        sev = (info.get("severity") or "UNKNOWN").upper()
        # `via` can be a list of strings or dicts; normalise
        cves = []
        for entry in info.get("via") or []:
            if isinstance(entry, dict):
                cves.append(entry.get("source") or entry.get("title"))
        cve = ", ".join(str(c) for c in cves if c) or None
        out.append(DependencyVulnerability(
            source="npm-audit",
            package=pkg_name,
            installed_version=info.get("range"),
            severity=sev,
            cve=cve,
            advisory_url=None,
            fix_versions=[info.get("fixAvailable", {}).get("version")] if isinstance(info.get("fixAvailable"), dict) else [],
            summary=info.get("title"),
        ).model_dump())
    return out


# --- Registry --------------------------------------------------------------

_PARSERS: Dict[str, Callable[[str], Any]] = {
    "stryker_json": parse_stryker_json,
    "mutmut_summary": parse_mutmut_summary,
    "bandit_json": parse_bandit_json,
    "pip_audit_json": parse_pip_audit_json,
    "npm_audit_json": parse_npm_audit_json,
}


def get_parser(name: str) -> Optional[Callable[[str], Any]]:
    """Look up a registered parser by name. Returns None if unknown
    (caller should treat as 'no artifact emitted', not crash)."""
    return _PARSERS.get(name)


def list_parsers() -> List[str]:
    """Available parser names — useful for debug + future skills doc."""
    return sorted(_PARSERS.keys())
