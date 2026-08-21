"""Semgrep SAST tool for the Security Agent.

Runs `semgrep --config p/owasp-top-ten --json` for security-focused scanning.
Distinct from the code_review_agent's semgrep_tool which uses --config auto.
Degrades gracefully if semgrep CLI is not installed.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_SEMGREP_BIN = shutil.which("semgrep")


@tool
def run_semgrep_sast(target_path: str, rulesets: str = "p/owasp-top-ten") -> str:
    """Run Semgrep SAST scan with OWASP-focused rules.

    Args:
        target_path: Absolute path to the directory or file to scan.
        rulesets: Semgrep ruleset config (default: OWASP Top 10 rules).

    Returns:
        JSON string of security-focused Semgrep findings.
    """
    if not _SEMGREP_BIN:
        return json.dumps({
            "status": "unavailable",
            "message": "Semgrep CLI is not installed. Install with: pip install semgrep",
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
            [_SEMGREP_BIN, "--config", rulesets, "--json", "--quiet", str(target)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(target if target.is_dir() else target.parent),
        )

        if result.returncode not in (0, 1):
            return json.dumps({
                "status": "error",
                "message": f"Semgrep exited with code {result.returncode}: {result.stderr[:500]}",
                "findings": [],
            })

        raw = json.loads(result.stdout) if result.stdout else {}
        results = raw.get("results", [])

        findings = []
        for r in results:
            findings.append({
                "rule_id": r.get("check_id", ""),
                "severity": r.get("extra", {}).get("severity", "WARNING").lower(),
                "message": r.get("extra", {}).get("message", ""),
                "file": r.get("path", ""),
                "line_start": r.get("start", {}).get("line", 0),
                "line_end": r.get("end", {}).get("line", 0),
                "owasp_category": r.get("extra", {}).get("metadata", {}).get("owasp", []),
                "cwe": r.get("extra", {}).get("metadata", {}).get("cwe", []),
            })

        return json.dumps({
            "status": "ok",
            "findings_count": len(findings),
            "findings": findings,
        })

    except subprocess.TimeoutExpired:
        return json.dumps({
            "status": "error",
            "message": "Semgrep SAST scan timed out after 120 seconds",
            "findings": [],
        })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Semgrep SAST scan failed: {str(e)[:300]}",
            "findings": [],
        })
