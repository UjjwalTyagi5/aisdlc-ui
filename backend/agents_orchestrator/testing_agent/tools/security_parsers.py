"""Trivy SARIF + Sonar JSON parsers — Phase B.3.

Extracts SecurityFinding records from artifacts published by an ADO pipeline.
The trigger_and_poll node (Nodes/pipeline.py) downloads artifacts named like
"trivy-report" / "sonar-report" / "*.sarif" / "*.json" and feeds them through
these parsers; the result populates state["security_findings"] which
artifact_builder rolls into TestingArtifact.security_findings.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from shared.models.testing import SecurityFinding


logger = logging.getLogger("testing_agent.security_parsers")


# ── Trivy SARIF v2.1 ────────────────────────────────────────────────────────

# SARIF severity levels → our normalised set.
_SARIF_LEVEL_TO_SEVERITY = {
    "error": "HIGH",
    "warning": "MEDIUM",
    "note": "LOW",
    "none": "INFO",
}


def parse_trivy_sarif(path: str) -> List[SecurityFinding]:
    """Parse a Trivy SARIF v2.1 file. Returns [] if the file doesn't exist or
    the JSON is malformed — never raises into the agent flow."""
    findings: List[SecurityFinding] = []
    if not os.path.exists(path):
        return findings
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.warning(f"parse_trivy_sarif: read/parse failed for {path}: {exc}")
        return findings

    runs = data.get("runs") or []
    for run in runs:
        # Build a rule-id → severity index from the tool's rules array.
        rules_index: Dict[str, str] = {}
        for rule in (run.get("tool", {}).get("driver", {}).get("rules") or []):
            rid = rule.get("id") or ""
            # Trivy stuffs severity in `properties.security-severity` (CVSS) or
            # `defaultConfiguration.level`. Prefer the SARIF-normalised level.
            level = rule.get("defaultConfiguration", {}).get("level")
            if level:
                rules_index[rid] = _SARIF_LEVEL_TO_SEVERITY.get(level, level.upper())

        for result in (run.get("results") or []):
            rule_id = result.get("ruleId") or ""
            level = result.get("level") or ""
            severity = (
                _SARIF_LEVEL_TO_SEVERITY.get(level)
                or rules_index.get(rule_id)
                or "MEDIUM"
            )
            message = (result.get("message") or {}).get("text") or ""

            # locations[0].physicalLocation.artifactLocation.uri
            file_uri = ""
            line: Optional[int] = None
            locations = result.get("locations") or []
            if locations:
                phys = locations[0].get("physicalLocation") or {}
                file_uri = (phys.get("artifactLocation") or {}).get("uri") or ""
                region = phys.get("region") or {}
                if "startLine" in region:
                    try:
                        line = int(region["startLine"])
                    except Exception:
                        line = None

            findings.append(SecurityFinding(
                source="trivy",
                severity=str(severity),
                rule_id=rule_id,
                file=file_uri,
                line=line,
                message=message,
                cwe=_extract_cwe(rule_id, message),
            ))
    logger.info(f"parse_trivy_sarif: extracted {len(findings)} findings from {path}")
    return findings


# ── Sonar /api/issues/search JSON shape ─────────────────────────────────────

_SONAR_SEVERITY_NORMALISE = {
    "BLOCKER": "CRITICAL",
    "CRITICAL": "CRITICAL",
    "MAJOR": "HIGH",
    "MINOR": "MEDIUM",
    "INFO": "LOW",
}


def parse_sonar_issues_json(path: str) -> List[SecurityFinding]:
    """Parse a SonarQube /api/issues/search response JSON. Never raises."""
    findings: List[SecurityFinding] = []
    if not os.path.exists(path):
        return findings
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.warning(f"parse_sonar_issues_json: read/parse failed for {path}: {exc}")
        return findings

    issues = data.get("issues") or []
    for issue in issues:
        severity = _SONAR_SEVERITY_NORMALISE.get(
            (issue.get("severity") or "").upper(), "MEDIUM"
        )
        rule_id = issue.get("rule") or ""
        message = issue.get("message") or ""

        # Sonar's `component` looks like "projectKey:relative/path/to/file"
        component = issue.get("component") or ""
        file_path = component.split(":", 1)[1] if ":" in component else component

        line: Optional[int] = None
        if "line" in issue:
            try:
                line = int(issue["line"])
            except Exception:
                line = None

        # CWE is sometimes carried in tags as "cwe-79" etc.
        cwe = _extract_cwe(rule_id, message, tags=issue.get("tags") or [])

        findings.append(SecurityFinding(
            source="sonar",
            severity=severity,
            rule_id=rule_id,
            file=file_path,
            line=line,
            message=message,
            cwe=cwe,
        ))
    logger.info(f"parse_sonar_issues_json: extracted {len(findings)} findings from {path}")
    return findings


# ── Helper ──────────────────────────────────────────────────────────────────

def _extract_cwe(rule_id: str, message: str, tags: Optional[List[str]] = None) -> Optional[str]:
    import re
    for source in (tags or []):
        m = re.match(r"cwe[-:]?(\d+)", str(source).lower())
        if m:
            return f"CWE-{m.group(1)}"
    for source in (rule_id, message):
        m = re.search(r"CWE[-:](\d+)", source)
        if m:
            return f"CWE-{m.group(1)}"
    return None


# ── Convenience wrapper used by Nodes/pipeline.py ───────────────────────────

def parse_artifacts_dir(artifacts_dir: str) -> List[SecurityFinding]:
    """Walk artifacts_dir, run Trivy parser on *.sarif / **trivy*.json and Sonar
    parser on **sonar*.json. Returns the combined list."""
    if not os.path.isdir(artifacts_dir):
        return []
    findings: List[SecurityFinding] = []
    for root, _, files in os.walk(artifacts_dir):
        for f in files:
            full = os.path.join(root, f)
            lower = f.lower()
            try:
                if lower.endswith(".sarif") or "trivy" in lower:
                    findings.extend(parse_trivy_sarif(full))
                elif "sonar" in lower and lower.endswith(".json"):
                    findings.extend(parse_sonar_issues_json(full))
            except Exception as exc:
                logger.warning(f"parse_artifacts_dir: {full} skipped ({exc})")
    return findings
