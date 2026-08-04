import type { Phase } from "@/lib/schemas/enums";

/**
 * The auto-sequencing Orchestrator — a cross-project cockpit that *runs* the
 * project's agent roster in hand-off order rather than waiting to be driven
 * stage by stage.
 *
 * NOTE ON PRD §34.11 — the per-project Orchestrator
 * (`app/(app)/projects/[id]/orchestrator/page.tsx`) is deliberately the
 * opposite of this: "a conversation partner, not an automatic sequencer.
 * Nothing runs a fixed script, and nothing auto-advances." That page is
 * untouched and remains the PRD-conformant surface. This one is the
 * agentcore-style cockpit — an explicit, opt-outable auto-sequencer — and the
 * two are separate routes precisely so the PRD reading is not overwritten by
 * this one.
 *
 * The one rule the sequencer never bends: a **mandatory** gate
 * (`GATE_POLICY[phase].mandatory`) always pauses the run. A mandatory
 * checkpoint cannot be waived by the owner or the fallback (PRD §13), so
 * auto-approving one would not be "faster" — it would be unrecoverable.
 */

/** Where one stage stands inside a single orchestrated run. */
export type StageRunStatus =
  | "pending"
  | "running"
  | "awaiting_gate"
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
   * Set on the turn that closes a stage. `decided` stays undefined while the
   * gate is open, which is what renders the inline approve/reject control.
   */
  gate?: {
    phase: Phase;
    mandatory: boolean;
    decided?: "approved" | "rejected";
  };
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
  /** Index into `stages` the sequencer is on. */
  cursor: number;
  /** Off → every gate pauses, mandatory or not (manual hand-off). */
  autoAdvance: boolean;
  status: SessionStatus;
}

export const modelKeyOf = (e: { provider: string; model_id: string }) =>
  `${e.provider}::${e.model_id}`;

export const splitModelKey = (key: string) => {
  const [provider = "", model_id = ""] = key.split("::");
  return { provider, model_id };
};
