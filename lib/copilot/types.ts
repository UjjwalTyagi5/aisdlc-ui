import { z } from "zod";

import { ARTIFACT_EVENTS } from "@/lib/copilot/artifacts";

/**
 * Copilot WS protocol — the Zod mirror of the backend `shared/models/copilot.py`
 * shapes and the Copilot WebSocket event envelopes.
 *
 * The Copilot page opens the FastAPI Copilot WS directly (via a short-lived,
 * single-use ticket minted server-side — see `/api/copilot/ws-ticket`), so —
 * unlike the SSE bridge (`ws-to-sse.ts`) — these events are consumed RAW. Every
 * inbound frame is validated with `CopilotEvent.safeParse` before it touches
 * component state; unrecognised frames are dropped (defense-in-depth, mirrors
 * the ws-to-sse discard rule).
 */

// ── Choice cards (mirror shared/models/copilot.py) ─────────────────────────────

export const ChoiceKind = z.enum([
  "ado_project",
  "story_multiselect",
  "repo",
  "branch",
  "confirm",
  "custom",
]);
export type ChoiceKind = z.infer<typeof ChoiceKind>;

export const ChoiceOption = z.object({
  id: z.string(),
  label: z.string(),
  sublabel: z.string().nullish(),
  meta: z.record(z.string(), z.unknown()).nullish(),
});
export type ChoiceOption = z.infer<typeof ChoiceOption>;

export const ChoiceCard = z.object({
  card_id: z.string(),
  run_id: z.string(),
  stage: z.string(),
  kind: ChoiceKind,
  prompt: z.string(),
  options: z.array(ChoiceOption).default([]),
  min_select: z.number().int().nonnegative().default(1),
  max_select: z.number().int().nonnegative().default(1),
});
export type ChoiceCard = z.infer<typeof ChoiceCard>;

export const ChoiceAnswer = z.object({
  card_id: z.string(),
  selected_ids: z.array(z.string()).default([]),
  free_text: z.string().nullish(),
});
export type ChoiceAnswer = z.infer<typeof ChoiceAnswer>;

// ── Gate state ─────────────────────────────────────────────────────────────

/** Chat-driven stage status — mirrors `stage_status_from_run` in progression.py. */
export const StageStatus = z.enum([
  "idle",
  "interviewing",
  "running",
  "awaiting_gate",
  "approved",
  "rejected",
  "complete",
]);
export type StageStatus = z.infer<typeof StageStatus>;

export const GateState = z.object({
  stage: z.string(),
  status: z.string().default("awaiting_gate"),
  owner_role: z.string(),
  can_approve: z.boolean().default(false),
});
export type GateState = z.infer<typeof GateState>;

// ── WS event envelopes (server → client) ──────────────────────────────────────

export const StreamChunkEvent = z.object({
  type: z.literal("stream_chunk"),
  content: z.string().default(""),
  session_id: z.string().optional(),
});

export const StreamEndEvent = z.object({
  type: z.literal("stream_end"),
  session_id: z.string().optional(),
});

export const ChoiceCardEvent = z.object({
  type: z.literal("choice.card"),
  run_id: z.string().optional(),
  card: ChoiceCard,
});

export const GateStateEvent = GateState.extend({
  type: z.literal("gate.state"),
});

export const StageChangedEvent = z.object({
  type: z.literal("stage.changed"),
  stage: z.string(),
});

/** A tool the active agent invoked — surfaced as an activity chip. */
export const ToolCallEvent = z.object({
  type: z.literal("tool.call"),
  run_id: z.string().optional(),
  name: z.string(),
  status: z.enum(["running", "done"]).default("running"),
});

/** Extended-thinking / reasoning delta, streamed before the visible answer. */
export const ThinkingEvent = z.object({
  type: z.literal("agent.thinking"),
  run_id: z.string().optional(),
  delta: z.string().default(""),
});

export const ErrorEvent = z.object({
  type: z.literal("error"),
  message: z.string().optional(),
  detail: z.string().optional(),
});

export const CopilotEvent = z.discriminatedUnion("type", [
  StreamChunkEvent,
  StreamEndEvent,
  ChoiceCardEvent,
  GateStateEvent,
  StageChangedEvent,
  ToolCallEvent,
  ThinkingEvent,
  ErrorEvent,
  // Artifacts panel stream (P1) — panel opens / builds / finalizes / lists.
  ...ARTIFACT_EVENTS,
]);
export type CopilotEvent = z.infer<typeof CopilotEvent>;

// ── WS messages (client → server) ──────────────────────────────────────────

export interface UserMessagePayload {
  type: "user_message";
  text: string;
  run_id: string;
  project_id?: string;
}

export interface ChoiceAnswerPayload {
  type: "choice_answer";
  card_id: string;
  selected_ids: string[];
  free_text?: string;
  run_id: string;
}

export interface GateDecisionPayload {
  type: "gate.decision";
  decision: "approved" | "rejected";
  stage: string;
  reason?: string;
  run_id: string;
}

export type CopilotOutbound =
  | UserMessagePayload
  | ChoiceAnswerPayload
  | GateDecisionPayload;

/** Shape returned by the BFF `/api/copilot/ws-ticket` mint route. */
export const CopilotTicket = z.object({
  ticket: z.string(),
  wsUrl: z.string(),
});
export type CopilotTicket = z.infer<typeof CopilotTicket>;
