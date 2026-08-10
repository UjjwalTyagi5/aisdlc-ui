import { GATE_POLICY, PHASE_LABEL } from "@/lib/agents";
import type { Phase } from "@/lib/schemas/enums";

/**
 * DUMMY-DATA SEAM — the Orchestrator's stage narration.
 *
 * There is no backend (standing directive), so an orchestrated run is scripted
 * here and revealed by the engine as if it were streaming. Every function takes
 * the run's context, so swapping this module for a real streaming endpoint is a
 * one-file change: the engine only ever asks for "what does this agent say, and
 * what did it produce".
 */

export interface StageScriptContext {
  projectName: string;
  /** What the user asked the Orchestrator to do, if anything. */
  objective: string;
  /** Human label of the model driving the run. */
  modelLabel: string;
}

/** Artifacts each agent claims to have produced — drives the stage rail. */
const STAGE_ARTIFACTS: Record<Phase, string[]> = {
  requirements: ["requirements.md", "user-stories.json", "traceability-matrix.csv"],
  design: ["hld.md", "lld.md", "c4-container.mmd", "openapi.yaml", "adr-001.md"],
  development: ["src/ (24 files changed)", "migration-0007.sql", "PR #482"],
  review: ["review-report.md", "12 inline comments"],
  security: ["sast-findings.json", "sbom.spdx.json", "risk-score.md"],
  testing: ["test-plan.md", "unit + integration suites", "coverage-report.html"],
  deployment: ["release-readiness.md", "deploy-plan.yaml", "rollback-runbook.md"],
  documentation: ["docs/ (9 pages)", "changelog.md", "api-reference.md"],
  discovery: ["as-is-inventory.csv", "assessment.md", "dependency-graph.mmd"],
  strategy: ["execution-plan.md", "wave-sequencing.csv", "risk-register.md"],
  migration_mapping: ["mapping-table.csv", "target-platform-notes.md"],
  validation: ["parity-report.md", "cutover-readiness.md"],
  data_engineering: ["pipeline.yaml", "schema-registry.json", "dq-checks.sql"],
};

/** The body of each agent's turn. Written as markdown — the thread renders it. */
const STAGE_BODY: Record<Phase, (c: StageScriptContext) => string> = {
  requirements: (c) =>
    `Read the intake for **${c.projectName}** and turned it into a baselined set.

- **18 requirements** extracted, 4 flagged ambiguous and resolved against the objective.
- **31 user stories** written with acceptance criteria, mapped 1:1 to a requirement.
- Non-functionals split out: availability, latency budget, data residency.

No requirement is left without a story, and no story without a parent — the traceability matrix is complete.`,

  design: (c) =>
    `Designed against the baselined requirements — nothing here is invented outside them.

- **C4 container view** with 6 services; the ingest path is separated from the query path so read load cannot starve writes.
- **API contract** in OpenAPI 3.1, 22 operations, all error shapes typed.
- **Schema**: 14 tables, 3 materialized views. Soft-delete everywhere audit matters.
- **ADR-001** records choosing an event log over dual-writes, and what it costs.

Every requirement from the previous stage is satisfied by at least one component of ${c.projectName}.`,

  development: () =>
    `Implemented the design in a sandboxed runtime with command allowlists and secret redaction on.

- **24 files changed**, +1,840 / −312.
- Migration \`0007\` adds the event log table and its partitioning.
- All 22 OpenAPI operations are wired; 4 are behind a feature flag pending Security.

Opened **PR #482**. It is *not* pushed to the protected branch — that is the gate below.`,

  review: () =>
    `Reviewed the change against the requirements, the design and the house standards.

- **12 inline comments**, 3 blocking: an unbounded query, a missing idempotency key, and one N+1 in the list endpoint.
- Design conformance: **passes** — no component drifted from the C4 view.
- Coverage of new code sits at 71%, below the 80% bar. Testing will close it.

The blocking three were fixed in-place and re-reviewed before I signed this off.`,

  security: () =>
    `Scanned dependencies, source and secrets.

- **SAST**: 0 critical, 2 high (both in a transitive parser dependency — pinned and patched).
- **Secrets**: none detected in the diff or in history for this branch.
- **SBOM** generated in SPDX; 3 packages carry licences that need legal review, none copyleft.

Composite risk score: **Low**. Nothing here blocks release.`,

  testing: () =>
    `Generated the plan from the requirements, then the suites from the code.

- **Test plan** covers all 31 stories plus 14 edge cases the stories did not state.
- Unit + integration suites written; **coverage now 86%** (was 71%).
- 2 failures found and traced to the flagged idempotency path — fixed, re-run green.

Every test maps back to a requirement id, so a failing test names the business rule it broke.`,

  deployment: () =>
    `Assembled release readiness — this stage validates, it does not ship.

- Build reproducible, image signed, provenance attested.
- **Risk gates**: migration is backward-compatible, so no downtime window is required.
- Rollback runbook written and dry-run against the staging snapshot.

Readiness: **green**. The actual promotion is a Consequential action and stays behind its gate.`,

  documentation: (c) =>
    `Compiled every upstream artifact into one consistent set for ${c.projectName}.

- **9 pages** — architecture, API reference, runbook, data model, decisions.
- Changelog assembled from the PR and the ADRs, not from commit messages.
- Cross-links resolved; no page references an artifact that does not exist.

This is the last stage in the roster.`,

  discovery: (c) =>
    `Built the as-is picture of ${c.projectName} before anyone plans against it.

- **142 components** inventoried across 6 runtimes; 23 have no owner recorded.
- Dependency graph resolved to 4 levels; 2 cycles found and documented.
- Assessment scores each component on change-frequency × blast-radius.`,

  strategy: () =>
    `Turned the assessment into a sequence that fails safely.

- **5 waves**, ordered so every wave's blast radius is smaller than the rollback window.
- The 2 dependency cycles are broken in wave 1 — nothing downstream can start until they are.
- Risk register carries 11 entries, each with an owner and a trigger.`,

  migration_mapping: () =>
    `Mapped each legacy item to its target, item by item — no bulk assumptions.

- **142 items** mapped: 96 like-for-like, 31 re-implemented, 15 retired outright.
- The 15 retirements are each justified against a usage signal, not an opinion.`,

  validation: () =>
    `Ran the legacy and target systems in parallel and compared.

- **7 days** of shadow traffic, 2.1M transactions compared.
- Parity: **99.97%**. The 640 divergences all trace to one rounding rule, now aligned.
- Cutover readiness: **accepted**.`,

  data_engineering: (c) =>
    `Built and registered the pipeline for ${c.projectName}.

- Ingest from 4 sources, transform in 3 stages, land in the warehouse schema.
- **Schema registered**; contracts versioned so a producer change cannot silently break a consumer.
- 18 data-quality checks wired as pipeline gates, not as after-the-fact alerts.`,
};

/** The Orchestrator's own opening turn, before the first agent speaks. */
export function openingTurn(c: StageScriptContext, roster: Phase[]): string {
  const objective = c.objective.trim();
  return `Picked up **${c.projectName}** on \`${c.modelLabel}\`.

${objective ? `Objective: _${objective}_\n\n` : ""}I will run the project's ${roster.length}-agent roster in hand-off order, passing each stage's artifacts to the next:

${roster.map((p, i) => `${String(i + 1).padStart(2, "0")}. ${PHASE_LABEL[p]}`).join("\n")}

Non-mandatory gates auto-approve as I go. **Mandatory** gates stop the run and wait for you — those cannot be waived.`;
}

/** What one agent says when its turn runs. */
export function stageTurn(phase: Phase, c: StageScriptContext): string {
  return STAGE_BODY[phase](c);
}

export function stageArtifacts(phase: Phase): string[] {
  return [...STAGE_ARTIFACTS[phase]];
}

/** The line the Orchestrator adds when it closes a gate on its own. */
export function autoApprovalNote(phase: Phase): string {
  const gate = GATE_POLICY[phase];
  return `Auto-approved the ${PHASE_LABEL[phase]} gate (${gate.capabilityClass}) — owner: ${gate.ownerLabel}. Handing off.`;
}

/** The line shown when the run stops at a gate that must be decided by a human. */
export function gatePrompt(phase: Phase): string {
  const gate = GATE_POLICY[phase];
  return `**${gate.title}**\n\n${gate.description}\n\nOwner: **${gate.ownerLabel}**.`;
}

/** A free-form reply when the user talks to the Orchestrator mid-run. */
export function conversationalReply(text: string, c: StageScriptContext): string {
  return `Noted — _"${text.trim()}"_.

I have carried that into the run context for **${c.projectName}**. It applies from the next stage onward; stages already approved are not re-run automatically. Ask me to restart the run if you need it applied from the top.`;
}
