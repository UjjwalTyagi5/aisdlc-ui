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
