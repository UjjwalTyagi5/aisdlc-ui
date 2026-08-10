"""Typed output artifact for the Security agent (`security_artifacts`).

Independent security gate over a branch/PR: layered scans (SCA/SAST/secrets/IaC/
container/license) → dedup → reachability + triage → risk score → remediation plan
→ signoff. Read-only on the repo; emits this structured artifact.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low", "info"]
FindingCategory = Literal[
    "sca", "sast", "secret", "iac", "container", "license", "supply_chain"
]
Reachability = Literal["reachable", "conditionally_reachable", "unreachable", "unknown"]
Triage = Literal["true_positive", "false_positive", "acceptable_risk", "unconfirmed"]
RiskScore = Literal["critical", "high", "medium", "low", "none"]
SignoffDecision = Literal["pass", "fail", "conditional"]


class ScanContext(BaseModel):
    repo_name: str = ""
    ado_project: str = ""
    mode: Literal["branch", "pr"] = "branch"
    branch: str = ""
    pr_id: Optional[str] = None
    pr_title: Optional[str] = None
    head_sha: str = ""


class SecurityFinding(BaseModel):
    id: str                          # S-001, S-002, …
    severity: Severity
    category: FindingCategory
    title: str
    cve: Optional[str] = None
    file: str = ""
    line: int = 0
    package: Optional[str] = None    # for SCA / supply-chain findings
    reachability: Reachability = "unknown"
    triage: Triage = "unconfirmed"
    description: str = ""
    remediation: str = ""
    autofix_patch: Optional[str] = None
    compliance: List[str] = Field(default_factory=list)   # e.g. ["OWASP A03:2021", "CWE-89"]


class SbomComponent(BaseModel):
    name: str
    version: str = ""
    license: Optional[str] = None
    vulnerabilities: int = 0


class SupplyChainNote(BaseModel):
    package: str
    risk: Severity = "low"
    note: str = ""


class SuppressionEntry(BaseModel):
    finding_id: str
    reason: str = ""


class Signoff(BaseModel):
    decision: SignoffDecision = "conditional"
    rationale: str = ""


class SecurityMetrics(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    total: int = 0


class SecurityArtifact(BaseModel):
    context: ScanContext = Field(default_factory=ScanContext)
    summary: str = ""                # markdown executive summary
    risk_score: RiskScore = "none"
    signoff: Signoff = Field(default_factory=Signoff)
    findings: List[SecurityFinding] = Field(default_factory=list)
    sbom: List[SbomComponent] = Field(default_factory=list)
    supply_chain: List[SupplyChainNote] = Field(default_factory=list)
    remediation_plan: str = ""       # markdown prioritized plan
    suppression_log: List[SuppressionEntry] = Field(default_factory=list)
    compliance_frameworks: List[str] = Field(default_factory=lambda: ["OWASP Top 10"])
    metrics: SecurityMetrics = Field(default_factory=SecurityMetrics)
    status: Literal["pending", "scanned"] = "scanned"
