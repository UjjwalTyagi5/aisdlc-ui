"""Trivy SCA/vulnerability scan tool for the Security Agent.

Runs `trivy fs --format json --scanners vuln` on a target directory.
Degrades gracefully if trivy CLI is not installed.

EXIT CODE 1 IS NOT ENOUGH TO CALL IT A SCAN. Trivy exits 1 both when it finds
vulnerabilities (with --exit-code) and on a FATAL error — for example Maven Central
answering 429 while Trivy resolves a pom.xml. The FATAL case prints no report, and
reading that as "ok, 0 findings" told a user their code was clean when it had not been
scanned at all. A run with no JSON report is an error, with Trivy's own message.

`offline` adds `--offline-scan`: no remote lookups while scanning (Maven Central for
POM resolution). Discovery uses it — a planning baseline must not depend on a public
repository's rate limit, and resolving a legacy pom remotely sends the organisation's
internal artifact names to that repository.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_TRIVY_BIN = shutil.which("trivy")


@tool
def run_trivy_scan(target_path: str, offline: bool = False) -> str:
    """Run Trivy vulnerability scan on the given directory.

    Args:
        target_path: Absolute path to the directory to scan.
        offline: Scan without remote lookups (declared dependencies only).

    Returns:
        JSON string of Trivy findings, or an error/unavailable message.
    """
    if not _TRIVY_BIN:
        return json.dumps({
            "status": "unavailable",
            "message": "Trivy CLI is not installed. Install from: https://github.com/aquasecurity/trivy",
            "findings": [],
        })

    target = Path(target_path)
    if not target.exists():
        return json.dumps({
            "status": "error",
            "message": f"Target path does not exist: {target_path}",
            "findings": [],
        })

    try:
        args = [_TRIVY_BIN, "fs", "--format", "json", "--scanners", "vuln", "--quiet"]
        if offline:
            args.append("--offline-scan")
        result = subprocess.run(
            [*args, str(target)],
            capture_output=True,
            text=True,
            timeout=180,
        )

        if result.returncode not in (0, 1) or not (result.stdout or "").strip():
            detail = (result.stderr or "").strip().splitlines()
            fatal = next((line for line in detail if "FATAL" in line), detail[-1] if detail else "")
            return json.dumps({
                "status": "error",
                "message": f"Trivy produced no report (exit code {result.returncode}): {fatal[:500]}",
                "findings": [],
            })

        raw = json.loads(result.stdout)
        results = raw.get("Results") or []

        findings = []
        for res in results:
            target_name = res.get("Target", "")
            for vuln in res.get("Vulnerabilities") or []:
                findings.append({
                    "cve": vuln.get("VulnerabilityID", ""),
                    "severity": vuln.get("Severity", "UNKNOWN").lower(),
                    "package": vuln.get("PkgName", ""),
                    "installed_version": vuln.get("InstalledVersion", ""),
                    "fixed_version": vuln.get("FixedVersion", ""),
                    "title": vuln.get("Title", ""),
                    "target": target_name,
                })

        return json.dumps({
            "status": "ok",
            "findings_count": len(findings),
            "findings": findings,
        })

    except subprocess.TimeoutExpired:
        return json.dumps({
            "status": "error",
            "message": "Trivy scan timed out after 180 seconds",
            "findings": [],
        })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Trivy scan failed: {str(e)[:300]}",
            "findings": [],
        })
