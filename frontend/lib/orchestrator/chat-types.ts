import { z } from "zod";

/**
 * The Orchestrator's chat vocabulary.
 *
 * Moved here in Phase 5 from `lib/copilot/types.ts`, keeping only what this surface
 * actually uses: choice cards, the Activity feed row, and the connection state the
 * panel's banner reads.
 *
 * WHAT WENT WITH THE COPILOT. The rest of that file was the Copilot's own WS protocol
 * — its event union, its gate state and gate-decision payloads, its outbound message
 * types and its ws-ticket shape. Nothing imported any of it once that surface was
 * deleted. The Orchestrator has its own union in `protocol.ts` and, by spec D5, no
 * gates at all, so keeping a `GateState` here would have been a type describing a
 * concept this product no longer has on this surface.
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

// ── live chat surface types ──────────────────────────────────────────────────
//
// Moved here in Phase 5 from `lib/copilot/use-copilot.ts`, which is deleted with the
// rest of that surface. They were always TYPES ONLY as far as the Orchestrator was
// concerned — it never used the hook — so they belong with the rest of its chat
// vocabulary rather than in a file about a retired engine.

/** WebSocket connection state, as the panel's banner reads it. */
export type ConnState =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "closed"
  | "error";

/** What kind of thing an Activity row records. */
export type ActivityKind = "tool" | "thinking" | "stage" | "turn";

/** One entry in the live agent-action feed (the panel's "Activity" tab). */
export interface ActivityItem {
  id: string;
  ts: string;
  kind: ActivityKind;
  label: string;
  status?: "running" | "done";
}
