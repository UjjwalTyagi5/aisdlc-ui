import { PHASE_LABEL } from "@/lib/agents";
import type { OrchestratorAgentId } from "@/lib/orchestrator/protocol";
import type { Phase } from "@/lib/schemas/enums";

/**
 * The Orchestrator — a cockpit that reaches every agent on a project (global
 * `/orchestrator`) or one project's agents (`/projects/[id]/orchestrator`,
 * the same component with the project fixed).
 *
 * Per PRD §34.11: "a conversation partner, not an automatic sequencer.
 * Nothing runs a fixed script, and nothing auto-advances." Any agent can pick
 * up work at any time, based on what the conversation asks for — there is no
 * hand-off order, no stage-index progression, and no gates or sign-off.
 * Project-Admin-only (`lib/orchestrator/access.ts`): driving it reaches every
 * agent on the project at once.
 */

/** Where one stage stands inside a single orchestrated run. */
export type StageRunStatus =
  | "pending"
  | "running"
  | "approved"
  | "rejected"
  | "skipped";

export interface StageRun {
  phase: Phase;
  status: StageRunStatus;
  /** Artifacts the stage claimed to produce — display only. */
  artifacts: string[];
  startedAt: number | null;
  finishedAt: number | null;
}

export type OrchestratorMessageRole = "user" | "agent" | "system";

export interface OrchestratorMessage {
  id: string;
  role: OrchestratorMessageRole;
  /** Which agent spoke. `null` = the Orchestrator itself, not a stage agent. */
  phase: Phase | null;
  content: string;
  createdAt: number;
  /** `provider::model_id` of the model that answered — agent turns only. */
  modelKey?: string | null;
  /**
   * Files that went out WITH this turn — user turns only.
   *
   * Not the run's attachment list, which is everything ever attached and is already
   * shown under the composer. This is the narrower claim the user needs once a turn
   * has gone: that these files were carried by THIS message. Stamped at send time, so
   * a file attached afterwards does not retroactively appear on an earlier turn.
   */
  attachments?: ReadonlyArray<{ name: string; url: string }>;
}

export type SessionStatus = "idle" | "running" | "paused" | "complete" | "failed";

/**
 * One orchestrated conversation. Persisted client-side (see
 * `stores/orchestrator-store.ts`) — there is no backend, per the standing
 * frontend-only directive.
 */
export interface OrchestratorSession {
  id: string;
  title: string;
  projectId: string;
  /** `provider::model_id`, or null until the project's default resolves. */
  modelKey: string | null;
  createdAt: number;
  updatedAt: number;
  messages: OrchestratorMessage[];
  stages: StageRun[];
  status: SessionStatus;
}

/**
 * Identifies a runnable model — provider, model, and WHICH SUBSCRIPTION.
 *
 * The credential is part of the identity because the same model can be served
 * by two keys with different endpoints, rates and limits. Keying on
 * provider+model alone made those one option, so a run could silently land on
 * the wrong contract — or the wrong region.
 *
 * The trailing segment is empty for an entry with no named subscription, which
 * keeps every key written before a provider could hold two parsing unchanged.
 */
export const modelKeyOf = (e: {
  provider: string;
  model_id: string;
  credentialId?: string | null;
}) => `${e.provider}::${e.model_id}::${e.credentialId ?? ""}`;

export const splitModelKey = (key: string) => {
  const [provider = "", model_id = "", credentialId = ""] = key.split("::");
  return { provider, model_id, credentialId: credentialId || null };
};

/**
 * The wire's agent id → the platform's phase id.
 *
 * Identical for eight of the nine. `code_review` is the exception: the engine's
 * registry calls it `code_review`, every phase-keyed surface in this app calls it
 * `review`, and mapping here is cheaper than renaming an identifier that sits in
 * route paths, artifact rows and the API contract.
 */
export const PHASE_FOR_AGENT: Record<OrchestratorAgentId, Phase> = {
  requirements: "requirements",
  design: "design",
  plan: "plan",
  development: "development",
  code_review: "review",
  security: "security",
  testing: "testing",
  deployment: "deployment",
  documentation: "documentation",
};

/**
 * What to CALL an agent in front of a user.
 *
 * Routed through `PHASE_LABEL` so there is exactly one answer per agent across
 * the app — which is how `plan` reads as "Project Manager" here, never "Plan"
 * and never "PM": the agent is named for the job it does, and only its internal
 * id says `plan`.
 */
export const agentLabel = (id: OrchestratorAgentId): string =>
  PHASE_LABEL[PHASE_FOR_AGENT[id]];
