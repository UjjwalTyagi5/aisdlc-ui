"""Semgrep SAST scan tool for the Code Review Agent.

Runs `semgrep --config auto --json` on a target directory and returns
structured findings. Degrades gracefully if semgrep is not installed.
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
def run_semgrep_scan(target_path: str, rulesets: str = "auto") -> str:
    """Run Semgrep SAST scan on the given directory or file path.

    Args:
        target_path: Absolute path to the directory or file to scan.
        rulesets: Semgrep ruleset config string (default: "auto" for community rules).

    Returns:
        JSON string of Semgrep findings, or an error/unavailable message.
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
            })

        return json.dumps({
            "status": "ok",
            "findings_count": len(findings),
            "findings": findings,
        })

    except subprocess.TimeoutExpired:
        return json.dumps({
            "status": "error",
            "message": "Semgrep scan timed out after 120 seconds",
            "findings": [],
        })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Semgrep scan failed: {str(e)[:300]}",
            "findings": [],
        })
