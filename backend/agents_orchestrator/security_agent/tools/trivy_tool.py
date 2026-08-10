"""Trivy SCA/vulnerability scan tool for the Security Agent.

Runs `trivy fs --format json --scanners vuln` on a target directory.
Degrades gracefully if trivy CLI is not installed.
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
def run_trivy_scan(target_path: str) -> str:
    """Run Trivy vulnerability scan on the given directory.

    Args:
        target_path: Absolute path to the directory to scan.

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
        result = subprocess.run(
            [_TRIVY_BIN, "fs", "--format", "json", "--scanners", "vuln", "--quiet", str(target)],
            capture_output=True,
            text=True,
            timeout=180,
        )

        if result.returncode not in (0, 1):
            return json.dumps({
                "status": "error",
                "message": f"Trivy exited with code {result.returncode}: {result.stderr[:500]}",
                "findings": [],
            })

        raw = json.loads(result.stdout) if result.stdout else {}
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
