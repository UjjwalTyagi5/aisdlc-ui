from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class DeploymentRequest(BaseModel):
    target_environment: Literal["dev", "staging", "production"] = "staging"
    deployment_strategy: Literal["rolling", "blue-green", "canary", "recreate"] = "rolling"
    artifact_type: Literal["docker", "zip", "k8s"] = "docker"
    env_vars: Dict[str, str] = Field(default_factory=dict)
    post_steps: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class DeploymentArtifacts(BaseModel):
    risk_analysis: Optional[str] = None
    deployment_validation: Optional[str] = None
    dockerfile: Optional[str] = None
    readme: Optional[str] = None
    docker_instructions: Optional[str] = None
    output_file_urls: List[str] = Field(default_factory=list)
    # New artifacts
    deployment_plan: Optional[str] = None
    docker_compose: Optional[str] = None
    cicd_pipeline_azure: Optional[str] = None
    cicd_pipeline_github: Optional[str] = None
    rollback_plan: Optional[str] = None
    deployment_decision: Optional[str] = None


class DeploymentResult(BaseModel):
    conversation_id: str
    deployment_decision: Optional[str] = None
    target_environment: str = "staging"
    deployment_strategy: str = "rolling"
    artifacts_generated: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)
    responses: str = ""
    output_filename: str = ""


# ── Standalone Deployment agent artifact (connector-driven; readiness + PR) ─────
# Distinct from the legacy `DeploymentArtifacts` above (the old readiness-evaluator).

_RiskScore = Literal["critical", "high", "medium", "low", "none"]


class DeployContext(BaseModel):
    repo_name: str = ""
    ado_project: str = ""
    mode: Literal["branch", "pr"] = "branch"
    source_branch: str = ""
    pr_id: Optional[str] = None
    head_sha: str = ""
    environment: str = "staging"
    deploy_via: Literal["azure_pipelines", "github_actions", "argocd", "unknown"] = "unknown"


class GatewayCheck(BaseModel):
    name: str
    status: Literal["pass", "fail", "unknown", "skipped"] = "unknown"
    note: str = ""


class GeneratedFile(BaseModel):
    path: str
    language: str = "yaml"
    contents: str = ""


class IacFinding(BaseModel):
    file: str = ""
    severity: _RiskScore = "low"
    rule: str = ""
    description: str = ""
    remediation: str = ""


class ComplianceEvidence(BaseModel):
    captured_at: str = ""
    gate_approvals: List[str] = Field(default_factory=list)
    test_summary: str = ""
    security_summary: str = ""
    sbom_present: bool = False
    notes: str = ""


class DeploymentArtifact(BaseModel):
    context: DeployContext = Field(default_factory=DeployContext)
    summary: str = ""
    readiness: Literal["ready", "blocked", "conditional"] = "conditional"
    risk_score: _RiskScore = "medium"
    risk_rationale: str = ""
    gate_summary: List[GatewayCheck] = Field(default_factory=list)
    generated_files: List[GeneratedFile] = Field(default_factory=list)
    deploy_runbook: str = ""
    rollback_runbook: str = ""
    iac_findings: List[IacFinding] = Field(default_factory=list)
    compliance_evidence: ComplianceEvidence = Field(default_factory=ComplianceEvidence)
    release_decision: Literal["go", "no_go", "conditional"] = "conditional"
    release_justification: str = ""
    pr_url: Optional[str] = None
    pr_title: Optional[str] = None
    status: Literal["assessed", "pr_opened"] = "assessed"
