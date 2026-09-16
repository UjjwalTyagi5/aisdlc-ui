"""Typed output artifact for the Security agent (`security_artifacts`).

Independent security gate over a branch/PR: layered scans (SCA/SAST/secrets/IaC/
container/license) → dedup → reachability + triage → risk score → remediation plan
→ signoff. Read-only on the repo; emits this structured artifact.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

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

    # A LIVE SUBMISSION WAS REFUSED FOR ITS SPELLING, NOT ITS CONTENT: an SCA finding has
    # no source line, and the model sent `"line": null`; it listed CVE ids as an array.
    # Neither is a different review, so neither is a reason to reject one.
    @field_validator("line", mode="before")
    @classmethod
    def _no_line(cls, v):
        return 0 if v in (None, "") else v

    @field_validator("cve", mode="before")
    @classmethod
    def _cve_list(cls, v):
        if isinstance(v, (list, tuple)):
            return ", ".join(str(x) for x in v if x) or None
        return v

    @field_validator("file", "description", "remediation", mode="before")
    @classmethod
    def _no_text(cls, v):
        return "" if v is None else v

    @field_validator("compliance", mode="before")
    @classmethod
    def _one_framework(cls, v):
        if v is None:
            return []
        return [v] if isinstance(v, str) else v


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
    #: The filed Security Review Report: {filename, url, artifact_id} — or {error}
    #: when it could not be written. Set by the API when the scan is saved.
    document: dict = Field(default_factory=dict)
    #: THE SCANNERS' OWN RESULTS (shared/services/code_security_scan): scanners with their
    #: status, vulnerabilities, secrets, static-analysis findings, the SBOM and totals.
    #: Kept apart from `findings`, which are the reviewer's triage: a reviewer may judge
    #: a CVE unreachable, but the scan that found it stays on the report unedited.
    scan: dict = Field(default_factory=dict)
