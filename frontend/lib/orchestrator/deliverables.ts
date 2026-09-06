import { z } from "zod";

import { ArtifactKind } from "@/lib/copilot/artifacts";
import { OrchestratorAgentId } from "@/lib/orchestrator/agents";

/**
 * Deliverables — what the Orchestrator's agents produce.
 *
 * NOT the `Artifact` the standalone agents write. That one carries an approval
 * status because a delivery role produced it and somebody has to accept it; the
 * Orchestrator is Project-Admin-only and has no gates, so its output is a separate
 * concept, in a separate table, with no approval anywhere in its shape. The two are
 * kept apart deliberately and must not be merged back together.
 *
 * Owned by `lib/orchestrator/`, not imported from `lib/copilot/`, which Phase 5
 * deletes. `ArtifactKind` is the one exception: it is the renderer registry's
 * vocabulary, shared by both surfaces, and moves here whole when the Copilot goes.
 *
 * NULLISH, NOT MERELY OPTIONAL. `url`, `language`, `source` and `created_at` are
 * nullable database columns and arrive as `null`. A schema that accepts `undefined`
 * but not `null` REJECTS the frame — and a rejected frame is a DROPPED frame, which
 * on screen is indistinguishable from an agent that produced nothing. This union has
 * already lost two event types exactly that way: an `ErrorEvent.agent` enum that
 * guaranteed the "unknown agent" error could never arrive, and a
 * `StreamChunkEvent.content` that dropped Anthropic's block-list content.
 */
export const Deliverable = z.object({
  id: z.string(),
  agent: OrchestratorAgentId,
  kind: ArtifactKind,
  title: z.string(),
  content: z.string().nullish().default(""),
  url: z.string().nullish(),
  language: z.string().nullish(),
  source: z.string().nullish(),
  /** ISO-8601. Null for synthesized pointers, which have no single moment. */
  created_at: z.string().nullish(),
});
export type Deliverable = z.infer<typeof Deliverable>;

/**
 * One turn's output, delivered whole.
 *
 * There is no `deliverable.open` / `.delta` / `.end` trio. The agent's text already
 * streams to chat, so a second streaming channel would carry nothing the user is not
 * already watching arrive — three event types with no data behind them, which is the
 * shape this codebase has repeatedly mistaken for a working feature.
 */
export const DeliverableReadyEvent = z.object({
  type: z.literal("deliverable.ready"),
  run_id: z.string().optional(),
  agent: OrchestratorAgentId,
  deliverables: z.array(Deliverable).default([]),
});
export type DeliverableReadyEvent = z.infer<typeof DeliverableReadyEvent>;

/** Ready to fold into the Orchestrator event union. */
export const DELIVERABLE_EVENTS = [DeliverableReadyEvent] as const;

/** `GET /api/runs/[id]/deliverables` → `{ deliverables: Deliverable[] }`. */
export const DeliverablesRead = z.object({
  deliverables: z.array(Deliverable).default([]),
});
export type DeliverablesRead = z.infer<typeof DeliverablesRead>;
