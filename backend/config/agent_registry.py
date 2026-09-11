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
    "plan": AgentDefinition(
        id="plan",
        name="Project Manager Agent",
        pipeline_position=3,
        # BOTH upstream artifacts, because a plan needs to know what is being built AND
        # how big it is. Requirements alone gives scope with no sizing; design alone
        # gives components with nothing tying them to what anybody asked for.
        input_artifacts=["requirements_payload", "design_artifacts"],
        output_artifact="plan_artifacts",
        route_path="/chat-project-manager-agent",
        # A plan commits people and dates. That is a decision somebody signs, not an
        # output that simply appears.
        gate_type="approval_required",
        sla_hours=24,
        can_parallel_with=[],
        max_rejections=1,
        required_capabilities=[
            "plan.wbs.generate", "plan.estimate", "plan.schedule.build",
            "board.read", "artifact.write",
        ],
        # CAPACITY IS OPTIONAL ON PURPOSE. Azure DevOps exposes it; Jira has no capacity
        # API at all (see the connector manifest). Requiring it would make the agent
        # unusable on Jira rather than merely less precise, so resource levelling
        # degrades to "no capacity data" instead of refusing to run.
        optional_capabilities=[
            "board.sprints.read", "board.capacity.read", "board.write",
            "plan.risk.track", "plan.report.status", "plan.rebaseline",
            # Costing is optional because the platform stores no labour rate: the
            # planner works without one, it simply cannot cost a plan until the user
            # supplies rates.
            "plan.cost.estimate", "plan.budget.read",
        ],
    ),
    "development": AgentDefinition(
        id="development",
        name="Development Agent",
        pipeline_position=4,
        # `plan_artifacts` is what makes the PM agent worth having: without a consumer
        # it would produce a schedule nothing reads. Development needs to know what was
        # committed to and in which sprint, not just what to build.
        input_artifacts=["requirements_payload", "design_artifacts", "plan_artifacts"],
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
        pipeline_position=5,
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
        pipeline_position=5,
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
        pipeline_position=5,
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
        pipeline_position=6,
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
        pipeline_position=7,
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
        # OPTIONAL, not required: filing documents to SharePoint depends on the tenant
        # having connected it. A tenant that has not is fully functional — docs still
        # save locally and still ship through a git PR.
        optional_capabilities=["docs.publish", "doc.ingest"],
    ),

    # ── Track 3 — Code Modernization (Portfolio 2) ─────────────────────────────
    #
    # Independent agents, NOT Portfolio 1's with a flag (multi-track-agent-access-
    # design.md §1.4). `pipeline_position` orders them within their own portfolio;
    # `stage_order_for_track("modernization")` never mixes them with Portfolio 1.
    "requirements_modernization": AgentDefinition(
        id="requirements_modernization",
        name="Requirements Agent (Migration Intent)",
        pipeline_position=1,
        input_artifacts=[],
        output_artifact="migration_intent_payload",
        route_path="/requirements-modernization",
        # Board writes are Consequential; baselining the brief is the Sign-off.
        gate_type="approval_required",
        sla_hours=24,
        can_parallel_with=[],
        max_rejections=1,
        required_capabilities=[
            "req.migration_intent.capture", "req.migration_intent.brief",
            "board.read", "artifact.write",
        ],
        optional_capabilities=["board.write", "doc.export.docx", "doc.export.pdf", "legacy.code.read"],
    ),
    "discovery": AgentDefinition(
        id="discovery",
        name="Discovery & Assessment Agent",
        pipeline_position=2,
        input_artifacts=["migration_intent_payload"],
        output_artifact="discovery_artifacts",
        route_path="/discovery",
        # "Accept the assessment as planning baseline" is a Sign-off.
        gate_type="approval_required",
        sla_hours=48,
        can_parallel_with=[],
        max_rejections=1,
        required_capabilities=[
            "discovery.repo.clone", "discovery.dependency.graph.build",
            "discovery.dependency.eol.scan", "discovery.dependency.cve.scan",
            "discovery.module.risk.score", "discovery.module.tier.classify",
            "artifact.write",
        ],
        optional_capabilities=["doc.export.docx", "doc.export.pdf", "legacy.code.read"],
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
# until its agents exist as AGENT_REGISTRY entries (spec Part 5 — an agent id is added
# here only once it's actually built and mounted). Modernization has its first two.
_PORTFOLIO_1: list[str] = [
    "requirements", "design", "plan", "development", "code_review",
    "security", "testing", "deployment", "documentation",
]

TRACK_PORTFOLIOS: dict[str, list[str]] = {
    "greenfield": _PORTFOLIO_1,
    "enhancement": _PORTFOLIO_1,
    # Built so far: the first two of Portfolio 2's ten (Phase 1). Design, Strategy and
    # the rest are added one at a time as each is built and mounted.
    "modernization": ["requirements_modernization", "discovery"],
    "rpa_infra": [],
    "data_engineering": [],
}


class UnknownTrackError(Exception):
    """Raised for a track string that is not one of `TRACK_PORTFOLIOS`'s five keys.

    A typo here must fail loudly rather than be treated as an empty portfolio —
    an empty portfolio is a real, meaningful state (a track with nothing built
    yet), and silently returning one for a MISSPELLED track name would look
    identical to that instead of surfacing the typo.
    """


def agents_for_track(track: str) -> dict[str, AgentDefinition]:
    """`AGENT_REGISTRY` filtered to the ids in `TRACK_PORTFOLIOS[track]`.

    THE ROUTING BOUNDARY THIS FUNCTION EXISTS TO DRAW: every agent Greenfield and
    Enhancement projects may ever be offered or run comes from here, not from
    `AGENT_REGISTRY` directly. Once Track 3/4/5 agents start landing in
    `AGENT_REGISTRY`, a caller that read the whole dict instead of going through
    this function would offer a Code Modernization agent to a Greenfield project's
    Orchestrator — this function is what a track-scoped caller (the orchestrator2
    router and dispatch) must call instead.

    Indexes `AGENT_REGISTRY` directly rather than `.get`-with-a-skip: an id present
    in `TRACK_PORTFOLIOS[track]` but absent from `AGENT_REGISTRY` is exactly the
    inconsistency the module docstring on `TRACK_PORTFOLIOS` promises never
    happens ("an agent id is added here only once it's actually built and
    mounted") — if it ever did, this must raise `KeyError` immediately, not
    silently drop the agent from the portfolio.
    """
    try:
        ids = TRACK_PORTFOLIOS[track]
    except KeyError:
        raise UnknownTrackError(
            f"unknown track {track!r}; known tracks: {sorted(TRACK_PORTFOLIOS)}"
        ) from None
    return {aid: AGENT_REGISTRY[aid] for aid in ids}


def stage_order_for_track(track: str) -> list[str]:
    """`get_pipeline_order`'s ordering, flattened and filtered to one track's
    portfolio — the same `(pipeline_position, agent_id)` tie-break `STAGE_ORDER`
    uses, applied to `agents_for_track(track)` instead of the whole registry."""
    agents = agents_for_track(track)
    return [
        aid
        for aid, _ in sorted(
            agents.items(), key=lambda kv: (kv[1].pipeline_position, kv[0])
        )
    ]

# ── Default role -> agent reach ───────────────────────────────────────────────
#
# ONE AGENT, ONE ROLE. A delivery role reaches exactly the agents it OWNS. There is
# no softer "use" tier any more: it let a role open an agent it did not own, and it
# had spread until a BA reached all nine, which is least privilege that never
# actually bites. "owner" = reaches it and approves its Consequential actions and
# Sign-offs. "none" = no default reach.
#
# EXTRA ACCESS IS A DELIBERATE ACT. `agent_access_overrides` is consulted BEFORE this
# table (shared/authz/agent_access.py), so a project admin grants an agent to a
# specific person on a specific project — visible, revocable and attributable, which
# a blanket "use" never was.
#
# project_admin is "owner" everywhere by design — the universal fallback approver —
# expressed as data here, not a code special-case, so there is exactly one source of
# truth for who reaches what.
#
# MUST MATCH `AGENT_OWNERSHIP` in frontend/lib/roles.ts. The UI greys out what this
# table denies; if they disagree, a user is either shown an agent the API will refuse
# or refused one the UI offered. `tests/test_agent_reach_matches_frontend.py` pins
# them together.
_OWNER_OF: dict[str, str] = {
    "requirements": "ba",
    "documentation": "ba",
    "design": "architect",
    "code_review": "architect",
    "development": "developer",
    "testing": "qa",
    "security": "security_engineer",
    "deployment": "devops_engineer",
    "plan": "scrum_master",
    # Track 3 — Code Modernization. BOTH owned by the BA — a product decision for this
    # track (2026-09-10) that departs from the design doc's "Discovery: Architect".
    # One agent, one role still holds: the Architect does not reach Discovery.
    "requirements_modernization": "ba",
    "discovery": "ba",
}

_DELIVERY_ROLES = (
    "ba", "architect", "developer", "qa", "security_engineer",
    "devops_engineer", "data_engineer", "scrum_master",
)

AGENT_DEFAULT_REACH: dict[str, dict[str, str]] = {
    agent: {
        "project_admin": "owner",
        **{role: ("owner" if role == owner else "none") for role in _DELIVERY_ROLES},
    }
    for agent, owner in _OWNER_OF.items()
}
