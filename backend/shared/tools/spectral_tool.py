"""Shared Spectral OpenAPI lint tool (reference §4.1 / §4.2 Step 5).

Runs `spectral lint --format json` on a referenced OpenAPI spec. Degrades gracefully
if the spectral CLI is not installed. Shared by the requirements and design agents.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_SPECTRAL_BIN = shutil.which("spectral")

_SEVERITY = {0: "error", 1: "warn", 2: "info", 3: "hint"}


@tool
async def run_spectral_lint(spec_path: str) -> str:
    """Lint an OpenAPI spec file with Spectral and return structured findings.

    Args:
        spec_path: Absolute path to the OpenAPI/Swagger spec (YAML or JSON).

    Returns:
        JSON string of lint findings, or an unavailable/error message.
    """
    if not _SPECTRAL_BIN:
        return json.dumps({
            "status": "unavailable",
            "message": "Spectral CLI not installed. Install with: npm i -g @stoplight/spectral-cli",
            "findings": [],
        })

    target = Path(spec_path)
    if not target.exists():
        return json.dumps({"status": "error", "message": f"Spec not found: {spec_path}", "findings": []})

    try:
        result = subprocess.run(
            [_SPECTRAL_BIN, "lint", "--format", "json", str(target)],
            capture_output=True, text=True, timeout=60,
        )
        raw = json.loads(result.stdout) if result.stdout.strip() else []
        findings = [{
            "code": r.get("code", ""),
            "severity": _SEVERITY.get(r.get("severity", 3), "hint"),
            "message": r.get("message", ""),
            "path": ".".join(str(p) for p in r.get("path", [])),
            "line": r.get("range", {}).get("start", {}).get("line", 0),
        } for r in raw]
        return json.dumps({"status": "ok", "findings_count": len(findings), "findings": findings})
    except subprocess.TimeoutExpired:
        return json.dumps({"status": "error", "message": "Spectral timed out after 60s", "findings": []})
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Spectral failed: {str(e)[:300]}", "findings": []})
