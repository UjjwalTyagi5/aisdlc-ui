import type { DeliveryTrack, Phase } from "@/lib/schemas/enums";
import type { CapabilityClass } from "@/lib/schemas/enums";
import { PHASE_DESCRIPTION, PHASE_LABEL, ROUTABLE_PHASES } from "@/lib/agents";
import { AGENT_OWNER_ROLE, ROLE_META, ROLE_ORDER, type PlatformRole } from "@/lib/roles";
import { TRACK_META, TRACK_ORDER, agentsForTrack, trackHasAgent } from "@/lib/tracks";

/**
 * The Agent Catalogue's content model — PRD-derived, and composed rather than
 * copied.
 *
 * SOURCING RULE, which the whole file obeys: anything the platform already
 * encodes is imported, never restated. Agent names and one-liners come from
 * `lib/agents.ts`; the owning role from `AGENT_OWNER_ROLE`; the roster per
 * track from `lib/tracks.ts`; the three capability classes from
 * `lib/capability-class.ts`; the twelve roles from `lib/roles.ts`. What is
 * added here is only what the PRD states and no module had yet: what each
 * agent consumes and produces *per track*, its approval flow, the business
 * outcome it serves, and the platform-level frames (autonomy ladder, risk
 * tiers, release lifecycle, governance mechanisms).
 *
 * That rule is the point. A catalogue that re-typed the agent list would
 * become the second source of truth for it, and the two would drift the first
 * time a track changed. Here, adding an agent to a track in `lib/tracks.ts`
 * changes this page with no edit.
 *
 * PROVENANCE: every exported block cites the PRD section it comes from. If a
 * fact is not in the PRD or the Modernization & Migration plan, it is not in
 * this file — there are no illustrative agents, invented tracks or sample
 * capabilities anywhere in the catalogue.
 */

// ─── Platform overview — PRD §1, §3, §5 ──────────────────────────────────────

/** PRD §1 "Product proposition", verbatim in substance. */
export const PLATFORM_PROPOSITION =
  "Turn demand into trusted production change — across the full software lifecycle — using specialised AI agents, while retaining human accountability, policy enforcement, traceability and operational control.";

/** PRD §5 "The platform in one page". */
export const PLATFORM_POSITIONING =
  "A client-deployed, single-tenant governed orchestration layer. Not a code-generation assistant — a configurable enterprise delivery platform.";

/** PRD §5 — the four facts that frame every journey through the platform. */
export const PLATFORM_FRAMING: { title: string; body: string }[] = [
  {
    title: "One control plane, five delivery tracks",
    body: "A track is a configurable template, not a separate product. It selects a context model, recommended agents and skills, required artifacts, evaluation suites, risk rules, an approval matrix and metrics.",
  },
  {
    title: "A four-level scope hierarchy",
    body: "Organization → Business Unit → Product/Application → Project → Workstream. Every protected object carries a scope, and access is granted strictly downward through it.",
  },
  {
    title: "The agents live inside a project",
    body: "Agents are never top-level pages. A person opens a project and, inside it, uses the agents their role permits.",
  },
  {
    title: "Human accountability by design",
    body: "Agents draft, read and analyse freely. Any write to an external system or irreversible action halts at an enforced gate, and formal acceptances are explicit sign-offs.",
  },
];

/** PRD §1 "Product Capabilities". */
export const PLATFORM_CAPABILITIES: { title: string; body: string }[] = [
  {
    title: "End-to-end delivery control",
    body: "Requirements, engineering, assurance, deployment and documentation are connected through traceable artifacts rather than independent point tools.",
  },
  {
    title: "Enterprise configurability",
    body: "Clients configure hierarchy, policies, roles, approval paths, data controls, models, tools, integrations, budgets and environments without product forks.",
  },
  {
    title: "Human accountability by design",
    body: "Consequential actions and formal sign-offs are governed at capability level, with evidence and immutable audit.",
  },
];

/** PRD §3 "Business outcomes" — outcome, mechanism and the KPI it is measured by. */
export const BUSINESS_OUTCOMES: {
  outcome: string;
  contribution: string;
  evidence: string;
}[] = [
  {
    outcome: "Faster flow",
    contribution:
      "Reduces context switching and automates repeatable analysis, creation and assurance work.",
    evidence: "Demand-to-PR lead time; PR-to-release lead time; throughput",
  },
  {
    outcome: "Higher quality",
    contribution: "Links requirements, design, code, tests and release evidence.",
    evidence: "Traceability coverage; escaped defects; test pass and coverage",
  },
  {
    outcome: "Lower delivery risk",
    contribution: "Applies policy before consequential actions and packages decision evidence.",
    evidence: "Policy violations; high-risk findings pre-production; approval exceptions",
  },
  {
    outcome: "Controlled AI economics",
    contribution: "Governs model, tool and execution consumption by scope.",
    evidence: "Cost per workstream/PR/release; budget variance; forecast accuracy",
  },
  {
    outcome: "Reusable capability",
    contribution: "Promotes approved skills, templates and integrations across teams.",
    evidence: "Reuse rate; onboarding time; adoption by business unit",
  },
];

// ─── Governance — PRD §13, §20.4 ─────────────────────────────────────────────

/**
 * PRD §13 "Autonomy ladder". The PRD is explicit that the platform's default
 * today is L1–L3 and that L4/L5 are controlled future capability with
 * eligibility criteria still open — `available` carries that distinction so the
 * page states it rather than implying five live levels.
 */
export const AUTONOMY_LADDER: {
  level: string;
  mode: string;
  useCase: string;
  available: boolean;
}[] = [
  { level: "L1", mode: "Assist", useCase: "Generate, analyse and explain; no external action", available: true },
  { level: "L2", mode: "Recommend", useCase: "Propose action plus evidence, impact and rationale", available: true },
  { level: "L3", mode: "Act with approval", useCase: "Execute a specific approved tool call or action", available: true },
  {
    level: "L4",
    mode: "Act within policy",
    useCase: "Execute pre-authorised, low-risk/reversible actions with continuous controls",
    available: false,
  },
  {
    level: "L5",
    mode: "Governed workflow automation",
    useCase:
      "Coordinate multiple steps with exception-based human intervention; approved track/risk combinations only",
    available: false,
  },
];

/** PRD §13 "Risk tiers". */
export const RISK_TIERS: { tier: string; definition: string }[] = [
  { tier: "R1", definition: "Internal advisory / low consequence; L1–L3 permitted under standard policy." },
  { tier: "R2", definition: "Internal delivery action; named owner and evidence required for consequential actions." },
  {
    tier: "R3",
    definition:
      "Customer-impacting, production or sensitive-data action; dual approval, formal evaluation, strict logging and rollback evidence.",
  },
  {
    tier: "R4",
    definition:
      "Regulated / high-consequence action; restricted operating mode, specialist approval quorum, fail-closed enforcement and enhanced audit.",
  },
];

/** PRD §20.4 — the six states every agent and skill configuration moves through. */
export const RELEASE_LIFECYCLE: { state: string; meaning: string }[] = [
  { state: "Draft", meaning: "Configured in a sandbox; no production data or tool access." },
  { state: "Validated", meaning: "Has passed functional, safety, security, policy and evaluation checks." },
  { state: "Approved", meaning: "Authorised for its defined scope and risk tier." },
  { state: "Published", meaning: "Available in the catalogue with an owner, a support model and a version record." },
  { state: "Monitored", meaning: "Quality, safety, reliability and cost are continuously observed." },
  { state: "Deprecated", meaning: "A replacement path is communicated, access removed, retention and audit preserved." },
];

/** PRD §13, §33.2, §34.9 — the enterprise controls wrapped around every agent. */
export const GOVERNANCE_CONTROLS: { title: string; body: string; href?: string }[] = [
  {
    title: "Capability classification",
    body: "Every agent action is Safe, Consequential or a Sign-off. Only the latter two ever require a human approval, and a sign-off is audited distinctly from a consequential approval.",
  },
  {
    title: "Approvals and named accountability",
    body: "Consequential actions and sign-offs route to the agent's owning role, with the Project Admin as fallback approver on every agent so work never stalls. No self-approval.",
    href: "/approvals",
  },
  {
    title: "Immutable audit trail",
    body: "Every consequential action and formal decision is recorded with its evidence packet, so control can be demonstrated to audit rather than asserted.",
    href: "/activity",
  },
  {
    title: "Policy packs",
    body: "Versioned, testable configurations covering data classification and residency, retention, approved models, tool constraints, action limits, environment rules, oversight requirements, risk thresholds, audit level and budget policy.",
  },
  {
    title: "Separation of duties",
    body: "Governance and delivery are two tiers that never cross within one scope: governance roles never build, and builders never approve their own work.",
    href: "/admin/access",
  },
  {
    title: "Cost and budget control",
    body: "Model, tool and execution consumption is governed by scope, with budgets that cascade from the organization down to a single project.",
    href: "/cost",
  },
];

// ─── Agents — PRD §20–§25 ────────────────────────────────────────────────────

/**
 * What an agent consumes and produces in a given track, and how its approval
 * flow runs. Straight from the per-agent sections of PRD §21–§25 — the "What it
 * does", "Produces" and "Typical approval flow" lines.
 */
export interface AgentTrackProfile {
  track: DeliveryTrack;
  /** The mode the PRD names for this agent in this track, when it names one. */
  mode?: string;
  /** What the agent consumes to start — PRD "What it does" / entry context. */
  inputs: string[];
  /** Artifacts the agent produces — PRD "Produces". */
  outputs: string[];
  /** PRD "Typical approval flow". */
  approvalFlow: string;
}

export interface CatalogueAgent {
  phase: Phase;
  name: string;
  /** One-line purpose — `PHASE_DESCRIPTION`, not restated. */
  purpose: string;
  /** Why it matters commercially — PRD §3 outcome this agent serves. */
  businessValue: string;
  /** Owning role — resolved from `AGENT_OWNER_ROLE`, never hardcoded. */
  ownerRole: PlatformRole;
  /** Which agent's artifact this one reads — PRD §20.2 ordered-stage rule. */
  dependsOn: Phase[];
  /** The capability classes this agent's actions span — PRD per-agent tables. */
  classes: CapabilityClass[];
  tags: string[];
  profiles: AgentTrackProfile[];
}

const A = (
  phase: Phase,
  businessValue: string,
  dependsOn: Phase[],
  classes: CapabilityClass[],
  tags: string[],
  profiles: AgentTrackProfile[],
): CatalogueAgent => ({
  phase,
  name: PHASE_LABEL[phase],
  purpose: PHASE_DESCRIPTION[phase],
  businessValue,
  ownerRole: AGENT_OWNER_ROLE[phase],
  dependsOn,
  classes,
  tags,
  profiles,
});

/**
 * The thirteen agents, in pipeline order.
 *
 * Thirteen is not a chosen number — it is every distinct agent named across
 * PRD §21–§25. Eight run the forward pipeline in Tracks 1 and 2; Tracks 3–5
 * add Discovery & Assessment, Strategy, Migration Mapping, Validation and Data
 * Engineering. An agent appearing in several tracks is one agent with several
 * profiles, exactly as the PRD frames it ("the same eight-agent portfolio…
 * entered at whichever stage the change actually requires").
 */
export const CATALOGUE_AGENTS: CatalogueAgent[] = [
  A(
    "requirements",
    "Shortens demand-to-PR lead time and makes requirements reusable and traceable into design, tests and delivery outcomes.",
    [],
    ["safe", "consequential", "signoff"],
    ["Jira", "Azure DevOps", "BRD", "User stories", "Gherkin"],
    [
      {
        track: "greenfield",
        inputs: ["Business intent, BRD, discovery inputs", "Meeting recordings, transcripts, documents", "Connected board (Jira / Azure DevOps)"],
        outputs: ["BRD, Process Definition Doc, Minutes of Meeting, risk register (.docx)", "INVEST user stories with Gherkin acceptance criteria", "Planning sheet (.xlsx), decks (.pptx), diagrams (.png)", "Persisted requirements artifact"],
        approvalFlow: "Draft & analyse (Safe) → approve each Jira/ADO write (Consequential) → baseline the requirements (Sign-off).",
      },
      {
        track: "enhancement",
        mode: "Triage & impact analysis",
        inputs: ["Incident, defect or change request", "Logs, existing code and runbooks"],
        outputs: ["Impact assessment and root-cause note", "Updated description and acceptance criteria on the existing work item", "Persisted triage artifact"],
        approvalFlow: "Draft & analyse (Safe) → approve each Jira/ADO write (Consequential) → baseline the triage/impact assessment (Sign-off).",
      },
      {
        track: "modernization",
        mode: "Migration-intent",
        inputs: ["Application inventory and business goal", "Scope boundary and constraints"],
        outputs: ["Migration-intent brief (goal, scope boundary, constraints, success criteria)", "Persisted requirements artifact consumed by Discovery & Assessment and Strategy"],
        approvalFlow: "Draft & analyse (Safe) → approve board writes (Consequential) → baseline the migration-intent brief (Sign-off).",
      },
      {
        track: "rpa_infra",
        mode: "Migration-intent by flavour",
        inputs: ["Bot inventory, process maps", "Infrastructure inventory and target systems"],
        outputs: ["Migration-intent brief (flavour per item, in-scope list, wave plan, constraints)", "Persisted requirements artifact consumed by Discovery & Assessment"],
        approvalFlow: "Draft & analyse (Safe) → approve board writes (Consequential) → baseline the migration-intent brief (Sign-off).",
      },
      {
        track: "data_engineering",
        mode: "Data intent",
        inputs: ["Source database / warehouse inventory", "Target analytics or reporting requirements", "Data-quality and cost baselines"],
        outputs: ["Data-intent requirements brief (source, target, quality and cost baselines)", "Persisted requirements artifact consumed by the Data Engineering Agent"],
        approvalFlow: "Draft & analyse (Safe) → approve board writes (Consequential) → baseline the data-intent requirements (Sign-off).",
      },
    ],
  ),
  A(
    "discovery",
    "Replaces guesswork with a risk-ranked, evidence-based view of the estate before anyone commits to a migration plan.",
    ["requirements"],
    ["safe", "signoff"],
    ["As-is assessment", "Dependency inventory", "Risk ranking", "Effort estimate"],
    [
      {
        track: "modernization",
        inputs: ["Repository, architecture, dependency and runtime data", "Migration-intent brief"],
        outputs: ["Structured as-is assessment — architecture summary, dependency inventory, risk-ranked module list, effort estimate"],
        approvalFlow: "Discover & assess (Safe) → accept the assessment as the planning baseline (Sign-off, Architect).",
      },
      {
        track: "rpa_infra",
        mode: "Three modes, by migration flavour",
        inputs: ["Bot code/configuration and exception logs", "Process maps", "Infrastructure inventory / IaC"],
        outputs: ["Per-item assessment — inventory, risk score, flagged credentials/secrets, migration disposition, extraction-method record"],
        approvalFlow: "Ingest & assess (Safe) → accept the assessment as the planning baseline (Sign-off, Architect).",
      },
    ],
  ),
  A(
    "design",
    "Grounds design in the existing landscape, standards, NFRs and policy — and never invents unstated requirements.",
    ["requirements", "discovery"],
    ["safe", "consequential", "signoff"],
    ["HLD", "LLD", "C4", "ADRs", "API contracts", "DB schema"],
    [
      {
        track: "greenfield",
        inputs: ["Requirements artifact, inherited automatically", "The existing codebase, to extend rather than rewrite"],
        outputs: ["8-section design document (.docx, .pptx) with embedded diagrams", "Epic state update — “Design Complete” plus document link", "Persisted design artifact handed to Development"],
        approvalFlow: "Generate & validate (Safe) → accept the design (Sign-off) → mark the epic “Design Complete” (Consequential). The sign-off comes first: you accept, then the acceptance is published.",
      },
      {
        track: "enhancement",
        mode: "Only when the change needs a design update",
        inputs: ["Triage artifact and impact assessment"],
        outputs: ["Design delta document, or the full 8-section package for larger changes", "Updated epic state", "Persisted design artifact"],
        approvalFlow: "Generate & validate (Safe) → accept the design (Sign-off) → mark the epic “Design Complete” (Consequential).",
      },
      {
        track: "modernization",
        mode: "Target architecture",
        inputs: ["As-is assessment from Discovery & Assessment"],
        outputs: ["Target architecture package — HLD, LLD, C4, API contracts, DB schema", "ADRs covering rewrite-vs-strangler-fig and target-stack decisions", "Persisted design artifact that Strategy sequences against"],
        approvalFlow: "Generate & validate (Safe) → accept the target design (Sign-off, Architect).",
      },
    ],
  ),
  A(
    "strategy",
    "Sequences the migration by risk so the smallest, most-depended-on modules move first and later work inherits proven ground.",
    ["design", "discovery"],
    ["safe", "signoff"],
    ["Execution plan", "Sequencing", "Equivalence criteria"],
    [
      {
        track: "modernization",
        inputs: ["Target design package", "As-is assessment"],
        outputs: ["Versioned execution plan per module — resolved sequencing, equivalence criteria, open decisions"],
        approvalFlow: "Draft the plan (Safe) → accept the migration plan (Sign-off, Architect) → the plan governs Development's build order.",
      },
    ],
  ),
  A(
    "migration_mapping",
    "Makes every legacy item's target explicit — and flags what has no clean equivalent instead of silently guessing.",
    ["discovery"],
    ["safe", "consequential", "signoff"],
    ["RPA-to-RPA", "RPA-to-Code", "Infrastructure mapping"],
    [
      {
        track: "rpa_infra",
        mode: "Three mapping strategies, by flavour",
        inputs: ["Per-item assessment from Discovery & Assessment"],
        outputs: ["Versioned per-item migration plan — IR/design, resolved mapping table or function design, open decisions"],
        approvalFlow: "Build the IR/design and resolve mappings (Safe) → resolve an escalated ambiguous mapping (Consequential, Architect) → accept the migration plan (Sign-off, Architect).",
      },
    ],
  ),
  A(
    "development",
    "Gives the developer relevant context and a reviewable change, while keeping human control of commits and PRs.",
    ["design", "strategy", "migration_mapping"],
    ["safe", "consequential", "signoff"],
    ["Sandboxed runtime", "Feature branch", "Draft PR", "Command allowlist"],
    [
      {
        track: "greenfield",
        inputs: ["Design artifact", "The project repository"],
        outputs: ["On-disk source code", "Feature branch, pushed branch and draft PR (Summary / Files Changed / How to Test / Work Items)", "Development-artifacts record and work-item state transitions"],
        approvalFlow: "Build in the sandbox (Safe) → approve push / open PR (Consequential — publishes for review) → accept the implementation (Sign-off). The write comes first here, because the PR is what Code Review reads.",
      },
      {
        track: "enhancement",
        inputs: ["Design delta or triage artifact", "Existing code"],
        outputs: ["Source code, pushed branch, draft PR", "Development-artifacts record and handoff to Testing"],
        approvalFlow: "Build in the sandbox (Safe) → approve push / open PR (Consequential) → accept the implementation (Sign-off).",
      },
      {
        track: "modernization",
        mode: "Per-module migration",
        inputs: ["Versioned execution plan from Strategy", "Legacy source"],
        outputs: ["Migrated source on isolated per-module branches", "Traceability map (old → new symbol/module)", "Pushed branch and draft PR once approved"],
        approvalFlow: "Build in the sandbox (Safe) → approve push / open PR (Consequential) → accept the migrated module (Sign-off, Architect).",
      },
      {
        track: "rpa_infra",
        mode: "Three build modes, by flavour",
        inputs: ["Per-item migration plan from Migration Mapping"],
        outputs: ["RPA-to-RPA: a structurally-validated target-platform project, deployed once approved", "RPA-to-Code: application code, unit tests and a scheduling wrapper", "Infrastructure: a provisioned target environment"],
        approvalFlow: "Build & validate in the sandbox (Safe) → approve the live production deploy/apply (Consequential, mandatory) → accept the migrated item (Sign-off, Architect).",
      },
    ],
  ),
  A(
    "data_engineering",
    "Turns a data intent into a running, lineage-documented pipeline with the performance and cost position stated up front.",
    ["requirements"],
    ["safe", "consequential", "signoff"],
    ["Snowflake", "Redshift", "Data lake", "JDBC", "Lineage", "Cost optimization"],
    [
      {
        track: "data_engineering",
        inputs: ["Data-intent requirements brief", "Source database / warehouse and existing job definitions"],
        outputs: ["Pipeline design document — source/target schema, mapping, lineage", "Generated pipeline code and connector configuration", "Performance-optimization report", "Cost-optimization report with projected savings"],
        approvalFlow: "Discover, profile, generate and report (Safe) → approve the connector registration and pipeline deployment (Consequential) → accept the pipeline (Sign-off, Data Engineer).",
      },
    ],
  ),
  A(
    "review",
    "Converts review from opinion to evidence — findings cited to file and line, checked against the requirements and the design.",
    ["development"],
    ["safe", "consequential", "signoff"],
    ["Severity tagging", "Requirements coverage", "Design conformance", "Merge recommendation"],
    [
      {
        track: "greenfield",
        inputs: ["The pull-request diff", "Requirements and design artifacts"],
        outputs: ["Severity/category-tagged findings with file and line citations", "Optional autofix patches — shown, never applied", "Requirements-coverage and design-conformance tables", "Change metrics and a merge recommendation"],
        approvalFlow: "Review the diff (Safe) → optionally approve posting comments/autofix (Consequential) → accept the review and its merge recommendation (Sign-off).",
      },
      {
        track: "enhancement",
        inputs: ["The pull-request diff for the change"],
        outputs: ["The same structured code-review artifact, scoped to the change"],
        approvalFlow: "Review the diff (Safe) → optionally approve posting comments (Consequential) → accept the review (Sign-off).",
      },
      {
        track: "modernization",
        inputs: ["Migrated module diff", "Target design package"],
        outputs: ["Findings plus a design-conformance table checked against the target design", "Merge recommendation"],
        approvalFlow: "Review the diff (Safe) → accept the review (Sign-off, Architect).",
      },
    ],
  ),
  A(
    "security",
    "Puts high-risk findings in front of a named approver before production, not after — and its sign-off gates deployment.",
    ["development", "review"],
    ["safe", "signoff"],
    ["SAST", "Dependency scanning", "Secret detection", "SBOM", "CVE reachability"],
    [
      {
        track: "greenfield",
        inputs: ["The repository and its dependency graph"],
        outputs: ["Risk score and PASS / FAIL / CONDITIONAL sign-off", "Findings with CVE, reachability, triage and remediation", "SBOM, supply-chain list, remediation plan, suppression register"],
        approvalFlow: "Run scans (Safe) → security sign-off, PASS/FAIL/CONDITIONAL (Sign-off, mandatory). There is no external write: the sign-off is the output, and it gates Deployment.",
      },
      {
        track: "enhancement",
        inputs: ["The changed code and its dependencies"],
        outputs: ["The same security artifact, scoped to the change"],
        approvalFlow: "Run scans (Safe) → security sign-off (Sign-off, mandatory).",
      },
      {
        track: "modernization",
        inputs: ["Migrated module or batch"],
        outputs: ["The same security artifact per module/batch", "A flag on any carried-over legacy vulnerability"],
        approvalFlow: "Run scans (Safe) → security sign-off (Sign-off, mandatory).",
      },
      {
        track: "rpa_infra",
        mode: "Pre-go-live gate",
        inputs: ["The migrated bot, code or environment", "Target-platform project (read-only)"],
        outputs: ["Credential/secret-provisioning confirmation", "Code or infrastructure scan findings", "Access-scope review and PASS/FAIL/CONDITIONAL sign-off"],
        approvalFlow: "Run the review (Safe) → security sign-off (Sign-off, mandatory). No external write; the sign-off gates Validation and, downstream, Deployment.",
      },
      {
        track: "data_engineering",
        mode: "PII / data-classification",
        inputs: ["The pipeline and its sources (read-only)"],
        outputs: ["The same security artifact, with data-classification findings as the primary content"],
        approvalFlow: "Run scans (Safe) → security sign-off (Sign-off, mandatory).",
      },
    ],
  ),
  A(
    "testing",
    "Produces the assurance evidence a release decision rests on, mapped back to the requirements it proves.",
    ["development", "requirements"],
    ["safe", "consequential", "signoff"],
    ["Test pyramid", "Coverage", "JUnit", "Mutation testing", "QA dashboard"],
    [
      {
        track: "greenfield",
        inputs: ["Requirements and the implemented code"],
        outputs: ["HTML + PDF QA dashboard — coverage, test pyramid, defects, security and mutation sections", "JUnit and coverage-report artifacts", "Generated test files per language", "Test plan (.xlsx) and UI results"],
        approvalFlow: "Generate the plan and tests (Safe) → approve the run — suites and live browser (Consequential) → accept the results (Sign-off). Running is not an external “ship”, so it is approved on its own.",
      },
      {
        track: "enhancement",
        mode: "Regression emphasis",
        inputs: ["The change and the existing suite"],
        outputs: ["The same QA dashboard and artifacts, with regression proof emphasised"],
        approvalFlow: "Generate (Safe) → approve the run (Consequential) → accept the results (Sign-off).",
      },
      {
        track: "modernization",
        mode: "Differential / equivalence testing",
        inputs: ["Legacy and migrated modules"],
        outputs: ["Consolidated migration validation report per module and batch — equivalence results, coverage, performance comparison, failures"],
        approvalFlow: "Generate the plan and run differential tests (Safe / Consequential) → accept the results (Sign-off, QA/Tester).",
      },
      {
        track: "data_engineering",
        mode: "Data quality",
        inputs: ["The generated pipeline and its data-quality scaffold"],
        outputs: ["Data-quality test report — pass/fail per check, coverage against the scaffold"],
        approvalFlow: "Generate/plan (Safe) → approve the run (Consequential) → accept the results (Sign-off).",
      },
    ],
  ),
  A(
    "validation",
    "Proves the migrated item behaves like the original by running both in parallel — the honest alternative to claiming equivalence.",
    ["development", "security"],
    ["consequential", "signoff"],
    ["Parallel parity", "Cutover readiness", "Credential verification"],
    [
      {
        track: "rpa_infra",
        inputs: ["The migrated bot, code or environment", "The legacy item still in service"],
        outputs: ["Consolidated validation report per item and per batch — parity/test results, flagged differences, credential verification, go/no-go recommendation"],
        approvalFlow: "Run parallel validation (Consequential — it drives a live run against the target) → accept the item as cutover-ready (Sign-off, QA/Tester).",
      },
    ],
  ),
  A(
    "deployment",
    "Packages the release decision with its evidence, so approving a deploy is a judgement on facts rather than on confidence.",
    ["testing", "security", "validation"],
    ["safe", "consequential", "signoff"],
    ["Release readiness", "Risk gates", "Rollback runbook", "Cutover plan"],
    [
      {
        track: "greenfield",
        inputs: ["Testing and security artifacts", "The release candidate"],
        outputs: ["Staged deployment package", "Structured deployment artifact — the release decision plus its evidence", "On explicit request only: a deployment PR and a real CI/CD trigger"],
        approvalFlow: "Build the package and assess readiness (Safe) → release sign-off, go/no-go (Sign-off, mandatory) → approve triggering the deploy (Consequential). The sign-off gates the write, because a deploy ships it.",
      },
      {
        track: "enhancement",
        inputs: ["Assurance evidence for the change"],
        outputs: ["The same release artifact and rollback runbook"],
        approvalFlow: "Stage & assess (Safe) → release sign-off (Sign-off, mandatory) → approve trigger (Consequential).",
      },
      {
        track: "modernization",
        mode: "Cutover",
        inputs: ["Migration validation report", "Security sign-off"],
        outputs: ["Staged release/cutover package", "Cutover sequence and a rollback plan specific to the migration"],
        approvalFlow: "Stage & assess (Safe) → release sign-off, go/no-go (Sign-off, mandatory) → approve trigger deploy/cutover (Consequential).",
      },
      {
        track: "rpa_infra",
        mode: "Wave-level cutover",
        inputs: ["Validation report per item and batch"],
        outputs: ["Wave-level cutover plan and go/no-go decision", "Decommission schedule for the legacy items", "Wave-level rollback runbook"],
        approvalFlow: "Plan the cutover and aggregate wave evidence (Safe) → cutover sign-off, go/no-go (Sign-off, mandatory) → approve triggering the cutover (Consequential).",
      },
      {
        track: "data_engineering",
        mode: "Pipeline orchestration",
        inputs: ["Data-quality evidence and security sign-off"],
        outputs: ["Staged pipeline deployment package", "Deployment artifact scoped to the pipeline schedule"],
        approvalFlow: "Stage & assess (Safe) → release sign-off (Sign-off, mandatory) → approve trigger (Consequential).",
      },
    ],
  ),
  A(
    "documentation",
    "Leaves the estate documented as a by-product of delivery rather than as a task nobody gets to.",
    ["deployment", "testing", "design"],
    ["safe", "consequential", "signoff"],
    ["Runbook", "Changelog", "Knowledge article", "Decommission plan"],
    [
      {
        track: "greenfield",
        inputs: ["Every upstream artifact in the project"],
        outputs: ["Consistent production documentation compiled from upstream artifacts", "Optionally a documentation PR"],
        approvalFlow: "Compile the docs (Safe) → approve the docs PR (Consequential) → acceptance is automatic (Sign-off, no human gate by default; manual override exists).",
      },
      {
        track: "enhancement",
        mode: "Runbook / knowledge-article update",
        inputs: ["The change and its evidence"],
        outputs: ["Updated runbook or knowledge article", "Changelog entry", "Optionally a documentation PR"],
        approvalFlow: "Compile (Safe) → approve the docs PR (Consequential) → acceptance is automatic (Sign-off, override exists).",
      },
      {
        track: "modernization",
        mode: "Cutover pack",
        inputs: ["Migration evidence and traceability map"],
        outputs: ["Modernization cutover pack — updated SDD, changelog, traceability map, equivalence evidence summary, decommission note"],
        approvalFlow: "Compile (Safe) → approve the docs PR (Consequential) → acceptance is automatic (Sign-off, override exists).",
      },
      {
        track: "rpa_infra",
        mode: "Decommission plan",
        inputs: ["Wave cutover evidence"],
        outputs: ["Migration summary", "Updated process/service runbook per migrated item", "Decommission plan for the legacy versions"],
        approvalFlow: "Compile (Safe) → approve the docs PR (Consequential) → acceptance is automatic (Sign-off, override exists).",
      },
      {
        track: "data_engineering",
        inputs: ["Pipeline design, lineage and classification findings"],
        outputs: ["Pipeline documentation", "Lineage diagram", "Data-classification register"],
        approvalFlow: "Compile (Safe) → approve the docs PR (Consequential) → acceptance is automatic (Sign-off, override exists).",
      },
    ],
  ),
];

/** Fast lookup by phase. */
export const AGENT_BY_PHASE = new Map(CATALOGUE_AGENTS.map((a) => [a.phase, a]));

/** Every agent that runs in a track, in that track's own order — from `lib/tracks.ts`. */
export function agentsInTrack(track: DeliveryTrack): CatalogueAgent[] {
  return agentsForTrack(track)
    .map((p) => AGENT_BY_PHASE.get(p))
    .filter((a): a is CatalogueAgent => a !== undefined);
}

/** The tracks an agent appears in — derived, so it cannot disagree with the roster. */
export function tracksForAgent(phase: Phase): DeliveryTrack[] {
  return TRACK_ORDER.filter((t) => trackHasAgent(t, phase));
}

/** An agent's profile within a track, if it runs there. */
export function profileFor(agent: CatalogueAgent, track: DeliveryTrack): AgentTrackProfile | undefined {
  return agent.profiles.find((p) => p.track === track);
}

/** Every agent whose owning role is this role — PRD Appendix B ownership matrix. */
export function agentsOwnedBy(role: PlatformRole): CatalogueAgent[] {
  return CATALOGUE_AGENTS.filter((a) => a.ownerRole === role);
}

/** All agents are routable today — kept as a derived signal rather than a literal. */
export function isAgentAvailable(phase: Phase): boolean {
  return ROUTABLE_PHASES.has(phase);
}

// ─── Delivery tracks — PRD §6, §7–§11 ────────────────────────────────────────

/**
 * Per-track detail the `TRACK_META` module does not carry: what starts the
 * track, what it must produce, and where it stops for a human. Straight from
 * the PRD §6 table ("Primary entry context", "Key outputs and gates") and the
 * per-track sections.
 */
export interface TrackDetail {
  track: DeliveryTrack;
  entryContext: string[];
  objectives: string[];
  deliverables: string[];
  /** The gates the PRD marks mandatory for this track. */
  checkpoints: string[];
}

export const TRACK_DETAIL: Record<DeliveryTrack, TrackDetail> = {
  greenfield: {
    track: "greenfield",
    entryContext: ["Business intent and BRD", "Discovery inputs", "Target operating constraints"],
    objectives: [
      "Build a blank-slate implementation through all eight agents in hand-off order.",
      "Connect requirements, design, code, tests and release evidence into one traceable line.",
    ],
    deliverables: ["Validated requirements", "Architecture and ADRs", "Pull request", "Assurance evidence", "Release pack", "Production documentation"],
    checkpoints: ["Requirements baselined (Sign-off)", "Design accepted (Sign-off)", "Security PASS/FAIL/CONDITIONAL (Sign-off, mandatory)", "Release go/no-go (Sign-off, mandatory)"],
  },
  enhancement: {
    track: "enhancement",
    entryContext: ["Incident, defect or change request", "Logs, existing code and runbooks"],
    objectives: [
      "Enter the same eight-agent portfolio at whichever stage the change actually requires.",
      "Prove the change is safe by regression rather than by re-building the whole delivery.",
    ],
    deliverables: ["Impact assessment", "Root cause", "Change evidence", "Regression proof", "Updated runbook or knowledge article"],
    checkpoints: ["Triage baselined (Sign-off)", "Security sign-off (mandatory)", "Release go/no-go (mandatory)"],
  },
  modernization: {
    track: "modernization",
    entryContext: ["Application inventory", "Repository, architecture, dependency and runtime data"],
    objectives: [
      "Take Track 1's eight-stage shape and add the two modernization-specific agents — ten in total.",
      "Move modules in risk order, proving behavioural equivalence rather than asserting it.",
    ],
    deliverables: ["Migration-intent brief", "As-is assessment", "Target architecture package (HLD/LLD/C4/ADRs)", "Versioned execution plan", "Migrated code with traceability map", "Equivalence validation report", "Cutover pack"],
    checkpoints: ["Assessment accepted as planning baseline (Sign-off)", "Target design accepted (Sign-off)", "Migration plan accepted (Sign-off)", "Security sign-off (mandatory)", "Cutover go/no-go (mandatory)"],
  },
  rpa_infra: {
    track: "rpa_infra",
    entryContext: ["Bot inventory and process maps", "Bot code/configuration and exception logs", "Infrastructure inventory / IaC and target systems"],
    objectives: [
      "Span three migration flavours — RPA-to-RPA, RPA-to-Code, and infrastructure migration — with one roster.",
      "Flag anything without a clean equivalent as a documented manual task instead of guessing.",
    ],
    deliverables: ["Migration-intent brief by flavour", "Per-item assessment", "Migration mapping and plan", "Migrated bot, code or provisioned environment", "Parallel-parity validation report", "Wave-level cutover plan", "Decommission plan"],
    checkpoints: ["Assessment accepted (Sign-off)", "Migration plan accepted (Sign-off)", "Live production deploy/apply (Consequential, mandatory)", "Security pre-go-live sign-off (mandatory)", "Cutover-ready (Sign-off)", "Wave cutover go/no-go (mandatory)"],
  },
  data_engineering: {
    track: "data_engineering",
    entryContext: ["Source database / warehouse inventory", "Existing pipeline and job definitions", "Target analytics or reporting requirements", "Data-quality and cost baselines"],
    objectives: [
      "Turn a data intent into a governed, lineage-documented production pipeline.",
      "State the performance and cost position explicitly, with projected savings.",
    ],
    deliverables: ["Pipeline design and lineage", "Generated connector configuration", "Performance-optimization report", "Cost-optimization report with projected savings", "Data-quality evidence", "Production-ready pipeline sign-off"],
    checkpoints: ["Data-intent requirements baselined (Sign-off)", "Connector registration and pipeline deployment (Consequential)", "Security PII/data-class sign-off (mandatory)", "Release sign-off (mandatory)"],
  },
};

// ─── Personas — PRD §2, §14, §15 ─────────────────────────────────────────────

/**
 * How each role uses the catalogue: the agents it owns, and the job the PRD
 * gives it. Roles and their one-liners come from `ROLE_META`; only the
 * catalogue-specific framing is added.
 */
export interface PersonaView {
  role: PlatformRole;
  /** PRD §2 "Success condition", where the PRD states one for this persona. */
  successCondition?: string;
}

/** PRD §2 persona table — "Success condition" column, where the PRD gives one. */
const PERSONA_SUCCESS: Partial<Record<PlatformRole, string>> = {
  org_admin: "Can change approved settings without code changes and demonstrate control to audit.",
  bu_admin: "Runs one unit's budget, connections, members and project creation without reaching a sibling unit.",
  project_admin: "Delivery moves without stalling — the fallback approver on every agent in the project.",
  ba: "Requirements are reusable and connected to design, tests and delivery outcomes.",
  architect: "Design is grounded in existing landscape, standards, NFRs and policy.",
  developer: "Receives relevant context, creates reviewable code, keeps human control of commits and PRs.",
  qa: "Can run deterministic controls, understand evidence and sign off only within assigned authority.",
  security_engineer:
    "Can run deterministic controls, understand evidence and sign off only within assigned authority.",
  devops_engineer:
    "Can run deterministic controls, understand evidence and sign off only within assigned authority.",
  data_engineer: "Owns the pipelines and connectors that Track 5 delivers, within delegated scope.",
  scrum_master: "Coordinates the team's flow across every agent stage; observes but owns no single gate.",
  custom: "A governed bundle of permissions and agent access, composed by an admin within their own scope.",
};

export const PERSONA_VIEWS: PersonaView[] = ROLE_ORDER.map((role) => ({
  role,
  successCondition: PERSONA_SUCCESS[role],
}));

// ─── Learning centre — PRD §38 four master journeys ──────────────────────────

/** PRD §2 "Four core journeys" / §38 "The four master journeys". */
export const MASTER_JOURNEYS: { title: string; body: string }[] = [
  {
    title: "Mobilise a workstream",
    body: "Select a track, ingest approved context, identify data classification and delivery risk, assign accountable roles and configure integrations.",
  },
  {
    title: "Build and assure",
    body: "Use agents in conversation or orchestrated sequence; generate artifacts; validate quality; collect evaluation and assurance evidence.",
  },
  {
    title: "Approve and release",
    body: "Give approvers a concise decision packet covering change, impact, evidence, policy results, dependencies, risk, cost and rollback.",
  },
  {
    title: "Operate and improve",
    body: "Monitor service health, outcome quality, defects, policy events, cost and adoption; improve agents, skills and policies through a governed release cycle.",
  },
];

/** Getting-started guidance, each step pointing at the screen that does it. */
export const GETTING_STARTED: { step: string; body: string; href?: string }[] = [
  {
    step: "Find the track that matches your demand",
    body: "A track is a template, not a product. Pick by what starts the work — a blank slate, an incident, a legacy estate, a bot inventory, or a data source.",
  },
  {
    step: "Open a project — agents live inside one",
    body: "Agents are never top-level pages. Open the project and use the agents your role permits.",
    href: "/projects",
  },
  {
    step: "Check what you may approve",
    body: "Your effective permissions and scope, in one place. Safe actions run immediately; Consequential and Sign-off actions route to a named approver.",
    href: "/my-access",
  },
  {
    step: "Work your approval queue",
    body: "Consequential writes and formal sign-offs arrive here with their evidence packet. The Project Admin is the fallback on every agent, so work never stalls.",
    href: "/approvals",
  },
  {
    step: "Read the evidence trail",
    body: "Every consequential action and formal decision is recorded, so control can be demonstrated rather than asserted.",
    href: "/activity",
  },
];

/** PRD §20.2 — what is true of every agent, in every track. */
export const AGENT_INVARIANTS: string[] = [
  "Each agent is a standalone agent-and-tool graph running over a repository, or a bot/process package for Track 4.",
  "All agents are read-only on the repository except for explicit, gated write actions.",
  "Every agent extends its native tools at runtime with per-agent skill tools and BYO MCP tools, and honours a per-Business-Unit prompt override.",
  "Agents read prior-stage outputs from the same tenant-scoped artifact store, so they behave as ordered pipeline stages even though progression is user-driven.",
];

export { TRACK_META, TRACK_ORDER, ROLE_META };
