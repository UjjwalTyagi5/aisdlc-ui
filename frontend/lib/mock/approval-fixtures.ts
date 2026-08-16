/**
 * Approval-inbox gates — plain data + pure functions, server-safe (imported by
 * the app/api/approvals route handlers). Deterministic except for
 * `requestedAt`/`deadline`, which are computed relative to now so the SLA
 * countdowns read realistically in a demo.
 *
 * The set below deliberately spans all five delivery tracks, all three
 * capability classes (PRD §13) and all three queue item types (§33.2), so the
 * Approvals screen exercises the real governance model rather than one happy
 * path.
 *
 * DUMMY-DATA source. The backend's gate state replaces the route
 * handlers, not these shapes.
 */
import type { ApprovalGate, ApprovalQueueMetrics, Phase } from "@/lib/schemas";
import { AGENT_OWNER_ROLE, ROLE_META } from "@/lib/roles";
import { GATE_POLICY } from "@/lib/agents";

/**
 * The role a gate routes to, derived from the PRD ownership matrix (§14.7)
 * rather than hardcoded. Approvals go sideways to the agent's owner — so this
 * can only ever name one of the platform's twelve roles.
 */
export function roleForPhase(phase: Phase): string {
  return ROLE_META[AGENT_OWNER_ROLE[phase]].label;
}

/**
 * Legacy lookup kept for callers that only know the permission string.
 * Values now come from the ownership matrix, so "Product Manager", "Tech Lead"
 * and "SRE Lead" — none of which are platform roles — no longer appear.
 */
export const ROLE_FOR_PERMISSION: Record<string, string> = {
  "artifact:approve_requirements": roleForPhase("requirements"),
  "artifact:approve_design": roleForPhase("design"),
  "artifact:approve_development": roleForPhase("development"),
  "artifact:approve_testing": roleForPhase("testing"),
  "artifact:approve_deployment": roleForPhase("deployment"),
};

function minutesAgo(m: number): string {
  return new Date(Date.now() - m * 60_000).toISOString();
}

function minutesAhead(m: number): string {
  return new Date(Date.now() + m * 60_000).toISOString();
}

interface Seed {
  id: string;
  type: ApprovalGate["type"];
  runId: string;
  projectId: string;
  projectName: string;
  phase: Phase;
  agentType: ApprovalGate["agentType"];
  permission: string;
  title: string;
  summary: string;
  requestedBy: string;
  ageMin: number;
  slaInMin?: number;
  artifact?: { id: string; title: string; type: string };
  question?: string;
  /** Overrides the gate policy's class — e.g. a Consequential external write. */
  capabilityClass?: ApprovalGate["capabilityClass"];
}

const SEEDS: Seed[] = [
  // ── Track 1 · Greenfield — Consequential external write (push / open PR).
  // The Developer built it; the Architect approves. Never self-approval.
  {
    id: "run_3101:development",
    type: "approval",
    runId: "run_3101",
    projectId: "mobile-onboarding",
    projectName: "Mobile onboarding journey",
    phase: "development",
    agentType: "development",
    permission: "artifact:approve_development",
    title: "Push branch and open PR — KYC capture step",
    summary:
      "Development agent built the KYC capture step in the sandbox and prepared a draft PR against feature/kyc-capture. Approving pushes the branch and opens the PR in Azure DevOps.",
    requestedBy: "Diego Alvarez (Developer)",
    ageMin: 34,
    slaInMin: 90,
    capabilityClass: "consequential",
    artifact: {
      id: "art_pr_3101",
      title: "PR #482 — KYC capture step",
      type: "pr",
    },
  },

  // ── Track 2 · Enhancement — mandatory security Sign-off, already overdue so
  // the Dashboard attention list has a real overdue item.
  {
    id: "run_3102:security",
    type: "approval",
    runId: "run_3102",
    projectId: "payments-api",
    projectName: "Payments API — SCA exemption defect",
    phase: "security",
    agentType: "security",
    permission: "artifact:approve_deployment",
    title: "Security sign-off — SCA exemption fix",
    summary:
      "Scan complete: 0 critical, 2 medium (both accepted risk, documented). SBOM built and reachability confirmed. A mandatory gate — a failing verdict blocks deployment and cannot be waived.",
    requestedBy: "Security Agent",
    ageMin: 260,
    slaInMin: -80,
    artifact: {
      id: "art_sec_3102",
      title: "Security verdict — PASS (conditional)",
      type: "review_comment",
    },
  },

  // ── Track 3 · Modernization — Strategy sign-off (Architect).
  {
    id: "run_3103:strategy",
    type: "approval",
    runId: "run_3103",
    projectId: "core-ledger",
    projectName: "Core ledger — Java 8 to 21",
    phase: "strategy",
    agentType: "strategy",
    permission: "artifact:approve_design",
    title: "Accept the migration plan — 14 modules",
    summary:
      "Strategy agent sequenced 14 modules lowest-risk first and defined equivalence criteria per module. Three undocumented business rules are flagged as open questions rather than assumed.",
    requestedBy: "Strategy Agent",
    ageMin: 95,
    slaInMin: 220,
    artifact: {
      id: "art_plan_3103",
      title: "Execution plan v3 — risk-sequenced",
      type: "adr",
    },
  },

  // ── Track 4 · RPA — a clarification, routed to whoever is running the agent.
  {
    id: "run_3104:migration_mapping",
    type: "clarification",
    runId: "run_3104",
    projectId: "recon-bots",
    projectName: "Reconciliation bots — A360 to UiPath",
    phase: "migration_mapping",
    agentType: "migration_mapping",
    permission: "artifact:approve_design",
    title: "Agent question — unmapped A360 action",
    summary:
      "Migration Mapping paused on an ambiguous action rather than guessing. It resumes exactly where it left off once answered.",
    requestedBy: "Migration Mapping Agent",
    ageMin: 12,
    slaInMin: 48,
    question:
      "Bot RECON-07 uses an Automation Anywhere 'Terminal Emulator' action with no deterministic UiPath equivalent. Should I map it to the UiPath Terminal activity package, or flag RECON-07 for RPA-to-Code instead?",
  },

  // ── Track 5 · Data engineering — Consequential connector registration.
  {
    id: "run_3105:data_engineering",
    type: "approval",
    runId: "run_3105",
    projectId: "fraud-features",
    projectName: "Fraud feature store pipeline",
    phase: "data_engineering",
    agentType: "data_engineering",
    permission: "artifact:approve_development",
    title: "Register Snowflake connector and schedule pipeline",
    summary:
      "Profiling complete across 4 source tables (18 fields classified, 3 PII). Approving registers the Snowflake connector and schedules the incremental load — both writes to an external system.",
    requestedBy: "Wei Chen (Data Engineer)",
    ageMin: 51,
    slaInMin: 130,
    capabilityClass: "consequential",
    artifact: {
      id: "art_pipe_3105",
      title: "card_auth_features — incremental ELT",
      type: "pipeline",
    },
  },

  // ── The third queue type: the outcome of something you raised (§33.2).
  {
    id: "req_3106:outcome",
    type: "outcome",
    runId: "run_3106",
    projectId: "mobile-onboarding",
    projectName: "Mobile onboarding journey",
    phase: "requirements",
    agentType: "requirements",
    permission: "artifact:view",
    title: "Your sandbox promotion was approved",
    summary:
      "Your Requirements skill change was promoted from your sandbox to the project default by Priya Menon (BA). Four-eyes satisfied — you were the requester, so you were not the approver.",
    requestedBy: "Agent Studio",
    ageMin: 180,
  },
];

export const GATES: ApprovalGate[] = SEEDS.map((s) => {
  const policy = GATE_POLICY[s.phase];
  return {
    id: s.id,
    type: s.type,
    runId: s.runId as ApprovalGate["runId"],
    projectId: s.projectId as ApprovalGate["projectId"],
    projectName: s.projectName,
    phase: s.phase,
    agentType: s.agentType,
    requiredPermission: s.permission,
    // Routed by ownership, never by job title.
    waitingForRole: roleForPhase(s.phase),
    capabilityClass: s.capabilityClass ?? policy.capabilityClass,
    mandatory: policy.mandatory,
    title: s.title,
    summary: s.summary,
    requestedBy: s.requestedBy,
    requestedAt: minutesAgo(s.ageMin),
    deadline: s.slaInMin !== undefined ? minutesAhead(s.slaInMin) : null,
    artifact: s.artifact
      ? ({
          id: s.artifact.id,
          title: s.artifact.title,
          type: s.artifact.type,
        } as ApprovalGate["artifact"])
      : null,
    question: s.question ?? null,
  };
});

export function buildQueueMetrics(): ApprovalQueueMetrics {
  const approvals = GATES.filter((g) => g.type === "approval").length;
  const clarifications = GATES.filter((g) => g.type === "clarification").length;
  const oldestMinutes = GATES.reduce((max, g) => {
    const age = Math.round((Date.now() - new Date(g.requestedAt).getTime()) / 60_000);
    return Math.max(max, age);
  }, 0);
  return {
    approvals,
    clarifications,
    oldestMinutes,
    generatedAt: new Date().toISOString(),
  };
}
