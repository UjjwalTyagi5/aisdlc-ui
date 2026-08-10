"""Gitleaks secret detection tool for the Security Agent.

Runs `gitleaks detect --report-format json` on a target directory.
Degrades gracefully if gitleaks CLI is not installed.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_GITLEAKS_BIN = shutil.which("gitleaks")


@tool
def run_gitleaks_scan(target_path: str) -> str:
    """Run Gitleaks secret detection scan on the given directory.

    Args:
        target_path: Absolute path to the directory to scan.

    Returns:
        JSON string of detected secrets, or an error/unavailable message.
    """
    if not _GITLEAKS_BIN:
        return json.dumps({
            "status": "unavailable",
            "message": "Gitleaks CLI is not installed. Install from: https://github.com/gitleaks/gitleaks",
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
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as tmp:
            report_path = tmp.name

        result = subprocess.run(
            [_GITLEAKS_BIN, "detect", "--source", str(target),
             "--report-format", "json", "--report-path", report_path,
             "--no-git"],
            capture_output=True,
            text=True,
            timeout=120,
        )

        findings = []
        try:
            with open(report_path, "r") as f:
                raw = json.load(f)
            if isinstance(raw, list):
                for leak in raw:
                    findings.append({
                        "rule_id": leak.get("RuleID", ""),
                        "description": leak.get("Description", ""),
                        "file": leak.get("File", ""),
                        "line": leak.get("StartLine", 0),
                        "match": "REDACTED",
                        "entropy": leak.get("Entropy", 0),
                    })
        except (json.JSONDecodeError, FileNotFoundError):
            pass
        finally:
            Path(report_path).unlink(missing_ok=True)

        return json.dumps({
            "status": "ok",
            "findings_count": len(findings),
            "findings": findings,
        })

    except subprocess.TimeoutExpired:
        return json.dumps({
            "status": "error",
            "message": "Gitleaks scan timed out after 120 seconds",
            "findings": [],
        })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Gitleaks scan failed: {str(e)[:300]}",
            "findings": [],
        })
