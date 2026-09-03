import type { AgentType, CapabilityClass, Phase } from "@/lib/schemas";
import { AGENT_OWNER_ROLE, ROLE_META } from "@/lib/roles";

/**
 * Agent labels, ordering and gate policy.
 *
 * The full 13-agent portfolio spans five delivery tracks (PRD Part V §20.1).
 * Which agents a given project shows is a function of its `DeliveryTrack` —
 * see `lib/tracks.ts::agentsForTrack()`. This module owns the *presentation*
 * of an agent (label, gate semantics); `lib/tracks.ts` owns the *roster*.
 */

/**
 * The eight shared agents in Track 1 hand-off order (PRD §7) — the default
 * roster, and the one used by surfaces that are not yet track-aware.
 *
 * Prefer `agentsForTrack(project.track)` wherever a project is in scope:
 * a Track 4 project has no Design or Code Review stage, and a Track 3
 * project has two stages this list does not contain.
 */
export const PHASE_ORDER: readonly Phase[] = [
  "requirements",
  "design",
  "development",
  "review",
  "security",
  "testing",
  "deployment",
  "documentation",
] as const;

/**
 * EVERY agent, in pipeline-then-track order — what `PHASE_ORDER` is not.
 *
 * `PHASE_ORDER` is the greenfield pipeline: the stages a Track 1 project runs.
 * Anything describing the platform's agents as such (role ownership, a
 * capability matrix) needs all thirteen, and reaching for `PHASE_ORDER`
 * silently drops the five track-specific ones.
 */
export const PHASE_ALL: readonly Phase[] = [
  ...PHASE_ORDER,
  "discovery",
  "strategy",
  "migration_mapping",
  "validation",
  "data_engineering",
] as const;

/** Human-readable label per phase/agent — all 13. */
export const PHASE_LABEL: Record<Phase, string> = {
  requirements: "Requirements",
  design: "Design",
  development: "Development",
  review: "Code Review",
  security: "Security",
  testing: "Testing",
  deployment: "Deployment",
  documentation: "Documentation",
  discovery: "Discovery & Assessment",
  strategy: "Strategy",
  migration_mapping: "Migration Mapping",
  validation: "Validation",
  data_engineering: "Data Engineering",
};

/** One-line "what this agent does" — all 13, shown on hover wherever an
 *  agent is listed without room for the full gate description above. */
export const PHASE_DESCRIPTION: Record<Phase, string> = {
  requirements: "Turns intent and conversations into structured requirements and work items.",
  design: "Produces HLD/LLD, C4 diagrams, API contracts, database schema, and ADRs.",
  development: "Generates code in a sandboxed runtime with command allowlists and secret redaction.",
  review: "Reviews changes against requirements, design, and quality standards.",
  security: "Dependency scanning, SAST, and secret detection with risk scoring.",
  testing: "Generates test plans and edge cases mapped to requirements and code.",
  deployment: "Builds release-readiness reports and risk-gate validations.",
  documentation: "Compiles every upstream artifact into consistent docs.",
  discovery: "Builds the as-is inventory and assessment later agents plan against (Tracks 3–4).",
  strategy: "Turns the assessment into a risk-sequenced execution plan (Track 3).",
  migration_mapping: "Maps each legacy item to its target platform, item by item (Track 4).",
  validation: "Runs parallel-parity validation and accepts cutover-readiness (Track 4).",
  data_engineering: "Builds and registers the data pipeline — ingest, transform, schema (Track 5).",
};

/**
 * Agents that have gone through the full "properly rebuilt and verified" pass
 * (help/portfolio-1-agent-status.md, Part 5 of
 * multi-track-agent-access-design.md) and are safe to render as real, clickable
 * tiles instead of "Coming soon". The single source of truth for both the
 * project page's tile grid and any agent's own standalone page gate — grow
 * this list one entry at a time as each agent passes verification, never in
 * two places.
 *
 * requirements + design were added after help/requirements-design-e2e-plan.md
 * Phases 1-5. What "verified" means for those two, concretely -- each claim has a
 * test behind it, because "a file with the right name is there" is not evidence:
 *   · the board connector is acquired with BOTH project_id and agent_id, so the
 *     stage's grant actually resolves. It silently resolved to "no access" for
 *     every run before (backend/tests/test_connector_stage_scope.py)
 *   · the Requirements -> Design hand-off carries the payload across the
 *     runs -> agent_sessions mirror. It read zero rows under FORCE RLS before, so
 *     Design received nothing (backend/tests/test_requirements_to_design_handoff.py)
 *   · board writes are gated on the owning role in code, not in prompt text
 *     (backend/tests/test_consequential_gate.py)
 *   · a run's initiator cannot approve their own gate
 *     (backend/tests/test_gate_self_approval.py)
 *   · one tenant's MCP servers do not reach another tenant
 *     (backend/tests/test_mcp_tenant_isolation.py)
 *
 * review (Code Review) — help/portfolio-1-agent-status.md's real-logic pass already
 * proved the graph→tools→graph loop, real Semgrep execution, and real
 * `submit_code_review` persistence (backend/tests/test_code_review_agent_live_e2e.py).
 * What was added to close it out:
 *   · the workspace router (review/prepare, reviews, reviews/{id}, the ADO PR list)
 *     was gated only by project membership, not by AGENT_DEFAULT_REACH["code_review"]
 *     — a QA/Data Engineer/DevOps project member could reach it despite the PRD
 *     ownership matrix (§14.7) marking them "none"
 *     (backend/tests/test_code_review_workspace_agent_access.py)
 *   · "skips redundant re-review when nothing changed since the last pass" (PRD
 *     §21.4) had no implementation — review/prepare now checks the diff's head+base
 *     sha against the project's past reviews before staging a fresh one
 *     (backend/tests/test_code_review_workspace_unchanged_diff.py)
 */
export const BUILT_AGENTS: readonly Phase[] = [
  "requirements",
  "design",
  "security",
  "documentation",
  "development",
  "review",
];

/** Label per agent (phases + the orchestrator meta-agent). */
export const AGENT_LABEL: Record<AgentType, string> = {
  orchestrator: "Orchestrator",
  ...PHASE_LABEL,
};

/**
 * Phases that have a dedicated project sub-page. Phases not in this set still
 * appear in every list/pipeline, but render as non-clickable "upcoming" nodes
 * instead of linking to a 404.
 *
 * All 13 agents are routable — use `phaseRoute()` to build the URL, since two
 * phase ids are snake_case while their routes are kebab-case.
 */
export const ROUTABLE_PHASES: ReadonlySet<Phase> = new Set<Phase>([
  "requirements",
  "design",
  "development",
  "review",
  "security",
  "testing",
  "deployment",
  "documentation",
  "discovery",
  "strategy",
  "migration_mapping",
  "validation",
  "data_engineering",
]);

/**
 * Route segment for a phase's project sub-page.
 *
 * Three phases don't map 1:1 onto their id:
 *  - `review` → `code-review`  (the real screen; `/review` redirects here)
 *  - `migration_mapping` → `migration-mapping`
 *  - `data_engineering` → `data-engineering`
 */
export function phaseRoute(phase: Phase): string {
  switch (phase) {
    case "review":
      return "code-review";
    case "migration_mapping":
      return "migration-mapping";
    case "data_engineering":
      return "data-engineering";
    default:
      return phase;
  }
}

/** Full href for a phase's page within a project. */
export function phaseHref(projectId: string, phase: Phase): string {
  return `/projects/${projectId}/${phaseRoute(phase)}`;
}

/**
 * Gate semantics.
 *
 * `type` is retained for backwards compatibility with existing stage
 * surfaces; `capabilityClass` is the PRD-canonical classification (§13,
 * §32.2) and should be preferred by new code:
 *
 *  - approval_required → Consequential or Sign-off, waivable by the owner
 *  - mandatory         → Sign-off that cannot be waived (§44.5)
 *  - auto_approve      → Sign-off recorded automatically; an override exists
 *  - conditional       → blocks only if a threshold breaches
 */
export type GateType =
  | "approval_required"
  | "mandatory"
  | "auto_approve"
  | "conditional";

export interface GatePolicy {
  /** Gate semantics for this phase. */
  type: GateType;
  /**
   * PRD capability class (§13). `signoff` gates are audited distinctly from
   * `consequential` approvals.
   */
  capabilityClass: CapabilityClass;
  /**
   * Display label for the owning role — derived from the PRD ownership
   * matrix (§14.7) so it can never drift from the twelve-role catalogue.
   */
  ownerLabel: string;
  /** Short gate heading shown on the artifact. */
  title: string;
  /** What approving this gate means / unlocks. */
  description: string;
  /** Mandatory checkpoints cannot be waived by the owner or the fallback. */
  mandatory: boolean;
}

/** Owner label resolved from the PRD ownership matrix, never hardcoded. */
const owner = (phase: Phase): string => ROLE_META[AGENT_OWNER_ROLE[phase]].label;

/**
 * Default gate → owner + class, per PRD §14.7 (ownership) and §13
 * (classification). Each org may reassign an agent's approver for its own
 * project (§33.2); this is the shipped default.
 *
 * Every owner label below resolves from `AGENT_OWNER_ROLE`, so the gates can
 * only ever name one of the platform's twelve roles.
 */
export const GATE_POLICY: Record<Phase, GatePolicy> = {
  requirements: {
    type: "approval_required",
    capabilityClass: "signoff",
    ownerLabel: owner("requirements"),
    title: "Gate: baseline the requirements",
    description:
      "Sign-off baselines the requirements and unlocks Design. Writing the stories to Jira/ADO is a separate Consequential approval.",
    mandatory: false,
  },
  design: {
    type: "approval_required",
    capabilityClass: "signoff",
    ownerLabel: owner("design"),
    title: "Gate: accept the design",
    description:
      "Sign-off accepts the design and unlocks Development. Marking the epic “Design Complete” is a separate Consequential write.",
    mandatory: false,
  },
  development: {
    type: "approval_required",
    capabilityClass: "consequential",
    ownerLabel: owner("development"),
    title: "Gate: push / open PR",
    description:
      "The Architect approves — a Developer never approves their own push or PR (separation of duties, PRD §14.7).",
    mandatory: false,
  },
  review: {
    type: "approval_required",
    capabilityClass: "signoff",
    ownerLabel: owner("review"),
    title: "Gate: accept the review",
    description:
      "The Architect signs off the review findings and the merge recommendation.",
    mandatory: false,
  },
  security: {
    type: "mandatory",
    capabilityClass: "signoff",
    ownerLabel: owner("security"),
    title: "Gate: security sign-off (mandatory)",
    description:
      "PASS / FAIL / CONDITIONAL, issued by the Security Engineer. A hard gate — a failing verdict blocks deployment and cannot be waived.",
    mandatory: true,
  },
  testing: {
    type: "approval_required",
    capabilityClass: "signoff",
    ownerLabel: owner("testing"),
    title: "Gate: accept the test results",
    description:
      "QA / Tester accepts the results. Running suites, the live browser and CI triggers are separate Consequential approvals.",
    mandatory: false,
  },
  deployment: {
    type: "mandatory",
    capabilityClass: "signoff",
    ownerLabel: owner("deployment"),
    title: "Gate: release sign-off (mandatory)",
    description:
      "Go / no-go by the DevOps Engineer. Production always requires an explicit human go-ahead — no exceptions.",
    mandatory: true,
  },
  documentation: {
    type: "auto_approve",
    capabilityClass: "signoff",
    ownerLabel: owner("documentation"),
    title: "Gate: documentation",
    description:
      "Acceptance is automatic; the Project Admin holds an override. Opening the docs PR remains a Consequential action.",
    mandatory: false,
  },

  // ── Track-specific agents ──────────────────────────────────────────────────
  discovery: {
    type: "approval_required",
    capabilityClass: "signoff",
    ownerLabel: owner("discovery"),
    title: "Gate: accept the assessment",
    description:
      "The Architect accepts the as-is assessment as the planning baseline for every later agent (PRD §23.2, §24.2).",
    mandatory: false,
  },
  strategy: {
    type: "approval_required",
    capabilityClass: "signoff",
    ownerLabel: owner("strategy"),
    title: "Gate: accept the migration plan",
    description:
      "The Architect accepts the risk-sequenced execution plan and its per-module equivalence criteria (PRD §23.4).",
    mandatory: false,
  },
  migration_mapping: {
    type: "approval_required",
    capabilityClass: "signoff",
    ownerLabel: owner("migration_mapping"),
    title: "Gate: accept the migration plan",
    description:
      "The Architect accepts the per-item mapping. Every ambiguous mapping escalates as its own Consequential checkpoint (PRD §24.3).",
    mandatory: false,
  },
  validation: {
    type: "approval_required",
    capabilityClass: "signoff",
    ownerLabel: owner("validation"),
    title: "Gate: accept as cutover-ready",
    description:
      "QA / Tester accepts the parallel-parity result. Running the parallel validation is itself Consequential — it drives live runs (PRD §24.6).",
    mandatory: false,
  },
  data_engineering: {
    type: "approval_required",
    capabilityClass: "signoff",
    ownerLabel: owner("data_engineering"),
    title: "Gate: accept the pipeline as production-ready",
    description:
      "The Data Engineer accepts the pipeline. Registering a connector and deploying/scheduling the pipeline are separate Consequential approvals (PRD §25.2).",
    mandatory: false,
  },
};
