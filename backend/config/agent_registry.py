"""Agent registry — single source of truth for the SDLC pipeline.

Each AgentDefinition declares:
  - what artifact fields it reads from AgentSession (input_artifacts)
  - what artifact field it writes (output_artifact)
  - the React route path for direct navigation
  - orchestration metadata: gate_type, sla_hours, parallelism, rejection limits

To add a new agent, add one entry here. Nothing else changes.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class AgentDefinition:
    id: str
    name: str
    pipeline_position: int          # lower = earlier in the pipeline
    input_artifacts: List[str]      # AgentSession field names this agent reads
    output_artifact: Optional[str]  # AgentSession field name this agent writes
    route_path: str                 # React route prefix (matches App.js)
    gate_type: str = "approval_required"
    sla_hours: int = 24
    can_parallel_with: List[str] = field(default_factory=list)
    max_rejections: int = 1
    required_capabilities: List[str] = field(default_factory=list)
    optional_capabilities: List[str] = field(default_factory=list)


AGENT_REGISTRY: dict[str, AgentDefinition] = {
    "requirements": AgentDefinition(
        id="requirements",
        name="Requirements Agent",
        pipeline_position=1,
        input_artifacts=[],
        output_artifact="requirements_payload",
        route_path="/chat-ingestion-agent",
        gate_type="approval_required",
        sla_hours=24,
        can_parallel_with=[],
        max_rejections=1,
        required_capabilities=[
            "req.ingest", "req.quality.analyze", "req.gap.detect",
            "story.generate", "story.ac.normalize",
            "doc.generate.brd", "doc.generate.risk",
            "req.payload.build", "board.read", "artifact.write",
        ],
        optional_capabilities=[
            "board.write", "board.comment", "design.api.lint",
            "nfr.elicit", "traceability.map",
        ],
    ),
    "design": AgentDefinition(
        id="design",
        name="Design Architecture Agent",
        pipeline_position=2,
        input_artifacts=["requirements_payload"],
        output_artifact="design_artifacts",
        route_path="/chat-design-&-architecture-agent",
        gate_type="approval_required",
        sla_hours=48,
        can_parallel_with=[],
        max_rejections=1,
        required_capabilities=[
            "design.hld.generate", "design.lld.generate", "design.api.contract.generate",
            "design.api.lint", "design.schema.validate", "design.adr.generate",
            "design.diagram.render", "design.security.design.checklist",
            "traceability.map", "artifact.write",
        ],
        optional_capabilities=["design.tech.stack.recommend", "design.system.analyze"],
    ),
    "development": AgentDefinition(
        id="development",
        name="Development Agent",
        pipeline_position=3,
        input_artifacts=["requirements_payload", "design_artifacts"],
        output_artifact="development_artifacts",
        route_path="/chat-development-agent",
        gate_type="approval_required",
        sla_hours=72,
        can_parallel_with=[],
        max_rejections=1,
        required_capabilities=[
            "repo.read", "repo.write", "vcs.branch.create", "code.generate", "code.edit",
            "code.lint", "code.build", "vcs.commit", "vcs.pr.create", "artifact.write",
        ],
        optional_capabilities=["code.format", "code.execute", "test.run", "repo.search"],
    ),
    "testing": AgentDefinition(
        id="testing",
        name="Testing Agent",
        pipeline_position=4,
        input_artifacts=["requirements_payload", "design_artifacts", "development_artifacts"],
        output_artifact="testing_artifacts",
        route_path="/chat-testing-agent",
        # approval_required (was auto_approve): Testing must NOT auto-advance to Deployment
        # when a test run finishes — the user may want to run additional test types
        # (functional/browser, API) on the same branch first. The stage stays active and
        # waits for an explicit approval before moving on.
        gate_type="approval_required",
        sla_hours=24,
        can_parallel_with=[],
        max_rejections=0,
        required_capabilities=[
            "test.plan", "test.generate", "test.run", "test.coverage",
            "test.failure.analyze", "test.quality.gate.evaluate", "artifact.write",
        ],
        optional_capabilities=["test.api.contract.test", "test.qa.report"],
    ),
    "code_review": AgentDefinition(
        id="code_review",
        name="Code Review Agent",
        pipeline_position=4,
        input_artifacts=["requirements_payload", "design_artifacts", "development_artifacts"],
        output_artifact="code_review_artifacts",
        route_path="/chat-code-review-agent",
        gate_type="approval_required",
        sla_hours=24,
        can_parallel_with=["security"],
        max_rejections=1,
        required_capabilities=[
            "review.diff.analyze", "review.requirements.coverage.map",
            "review.design.conformance.check", "quality.sast.scan",
            "review.severity.assess", "review.merge.recommend", "artifact.write",
        ],
        optional_capabilities=["quality.complexity", "quality.dupe.detect", "vcs.pr.comment"],
    ),
    "security": AgentDefinition(
        id="security",
        name="Security Agent",
        pipeline_position=4,
        input_artifacts=["design_artifacts", "development_artifacts"],
        output_artifact="security_artifacts",
        route_path="/chat-security-agent",
        gate_type="mandatory",
        sla_hours=24,
        can_parallel_with=["code_review"],
        max_rejections=1,
        required_capabilities=[
            "quality.sca.scan", "quality.sbom.generate", "quality.sast.scan",
            "quality.secret.scan", "sec.finding.dedup", "sec.severity.contextualize",
            "sec.risk.score", "sec.remediation.plan", "sec.signoff", "artifact.write",
        ],
        optional_capabilities=["quality.iac.scan", "quality.license.scan", "sec.owasp.map"],
    ),
    "deployment": AgentDefinition(
        id="deployment",
        name="Deployment Agent",
        pipeline_position=5,
        input_artifacts=["development_artifacts", "testing_artifacts"],
        output_artifact="deployment_artifacts",
        route_path="/chat-deployment-agent",
        gate_type="mandatory",
        sla_hours=24,
        can_parallel_with=[],
        max_rejections=1,
        required_capabilities=[
            "deploy.readiness.assess", "deploy.gate.aggregate", "deploy.plan",
            "deploy.rollback.plan", "deploy.release.decision", "artifact.write",
        ],
        optional_capabilities=["deploy.iac.validate", "deploy.env.health.check", "deploy.pipeline.trigger"],
    ),
    "documentation": AgentDefinition(
        id="documentation",
        name="Documentation Agent",
        pipeline_position=6,
        input_artifacts=[
            "requirements_payload", "design_artifacts", "development_artifacts",
            "code_review_artifacts", "security_artifacts", "testing_artifacts",
            "deployment_artifacts",
        ],
        output_artifact="documentation_artifacts",
        route_path="/chat-documentation-agent",
        gate_type="auto_approve",
        sla_hours=24,
        can_parallel_with=[],
        max_rejections=0,
        required_capabilities=[
            "repo.read", "doc.changelog.generate", "artifact.read",
            "doc.generate", "vcs.pr.create",
        ],
        optional_capabilities=[],
    ),
}


def get_pipeline_order() -> List[List[str]]:
    """Derive ordered phases from registry, grouping agents with same pipeline_position."""
    by_pos: dict[int, list[str]] = defaultdict(list)
    for agent_id, defn in AGENT_REGISTRY.items():
        by_pos[defn.pipeline_position].append(agent_id)
    return [by_pos[pos] for pos in sorted(by_pos.keys())]


# ── Track portfolios (multi-track-agent-access-design.md §1.4) ─────────────────
#
# Each track owns its own agent list. Only Greenfield and Enhancement genuinely
# share one — both point at the same literal list below, not by convention but by
# construction, so they can never silently drift apart. Modernization, RPA/Infra
# Migration, and Data Engineering are independent portfolios; each starts empty
# because none of their agents exist as AGENT_REGISTRY entries yet (spec Part 5 —
# an agent id is added here only once it's actually built and mounted).
_PORTFOLIO_1: list[str] = [
    "requirements", "design", "development", "code_review",
    "security", "testing", "deployment", "documentation",
]

TRACK_PORTFOLIOS: dict[str, list[str]] = {
    "greenfield": _PORTFOLIO_1,
    "enhancement": _PORTFOLIO_1,
    "modernization": [],
    "rpa_infra": [],
    "data_engineering": [],
}

# ── Default role -> agent reach (multi-track-agent-access-design.md Appendix) ──
#
# Transcribed directly from PRD §14.7. "owner" = approves this agent's Consequential
# actions and Sign-offs. "build"/"requests" = does the hands-on work or asks for a
# specific action; a separate owner approves it. "use" = may chat/run Safe
# capabilities only. "none" = no default reach (an override can still grant it,
# per Task 3/4). project_admin is "owner" everywhere by design — the universal
# fallback approver — expressed as real data here, not a code special-case, so
# there is exactly one source of truth for who reaches what.
AGENT_DEFAULT_REACH: dict[str, dict[str, str]] = {
    "requirements": {
        "project_admin": "owner", "ba": "owner", "architect": "use",
        "developer": "use", "qa": "use", "security_engineer": "use",
        "devops_engineer": "none", "data_engineer": "use",
    },
    "design": {
        "project_admin": "owner", "ba": "use", "architect": "owner",
        "developer": "none", "qa": "none", "security_engineer": "use",
        "devops_engineer": "none", "data_engineer": "use",
    },
    "development": {
        "project_admin": "owner", "ba": "use", "architect": "owner",
        "developer": "build", "qa": "use", "security_engineer": "use",
        "devops_engineer": "requests", "data_engineer": "none",
    },
    "code_review": {
        "project_admin": "owner", "ba": "use", "architect": "owner",
        "developer": "requests", "qa": "none", "security_engineer": "use",
        "devops_engineer": "none", "data_engineer": "none",
    },
    "security": {
        "project_admin": "owner", "ba": "use", "architect": "use",
        "developer": "use", "qa": "use", "security_engineer": "owner",
        "devops_engineer": "use", "data_engineer": "use",
    },
    "testing": {
        "project_admin": "owner", "ba": "use", "architect": "use",
        "developer": "use", "qa": "owner", "security_engineer": "none",
        "devops_engineer": "use", "data_engineer": "use",
    },
    "deployment": {
        "project_admin": "owner", "ba": "use", "architect": "use",
        "developer": "none", "qa": "none", "security_engineer": "use",
        "devops_engineer": "owner", "data_engineer": "none",
    },
    "documentation": {
        "project_admin": "owner", "ba": "use", "architect": "use",
        "developer": "use", "qa": "use", "security_engineer": "none",
        "devops_engineer": "use", "data_engineer": "use",
    },
}
