import { z } from "zod";

import { ARTIFACT_EVENTS } from "@/lib/copilot/artifacts";
import { ChoiceCard } from "@/lib/copilot/types";

/**
 * Orchestrator wire protocol — the contract between the Orchestrator UI and the
 * engine built in Phase 2.
 *
 * It is deliberately the Copilot protocol MINUS gates, PLUS `agent.selected`:
 *
 *  - Gates are gone because the Orchestrator is Project-Admin-only and there is
 *    nobody to sign off to (spec D5). A gate event here is a backend bug, so the
 *    union rejects it rather than rendering an unusable control.
 *  - `agent.selected` is new because routing is now a visible decision. The old
 *    engine advanced by list index and told the user nothing; when it picked
 *    wrong, there was no way to see that it had. Every routing decision is
 *    announced with its reason so the user can redirect in one turn.
 *  - There is no `stage.changed`: "stage" implied a position in a fixed sequence.
 *    `agent.selected` carries the same information without the false ordering.
 */

/** The nine agents. `plan` is the Project Manager agent — internal id only. */
export const ORCHESTRATOR_AGENT_IDS = [
  "requirements",
  "design",
  "plan",
  "development",
  "code_review",
  "security",
  "testing",
  "deployment",
  "documentation",
] as const;

export const OrchestratorAgentId = z.enum(ORCHESTRATOR_AGENT_IDS);
export type OrchestratorAgentId = z.infer<typeof OrchestratorAgentId>;

export const StreamChunkEvent = z.object({
  type: z.literal("stream_chunk"),
  content: z.string().default(""),
  session_id: z.string().optional(),
});

export const StreamEndEvent = z.object({
  type: z.literal("stream_end"),
  session_id: z.string().optional(),
});

/** The router chose an agent. Announced so a wrong choice is visible immediately. */
export const AgentSelectedEvent = z.object({
  type: z.literal("agent.selected"),
  agent: OrchestratorAgentId,
  reason: z.string().default(""),
  run_id: z.string().optional(),
});
export type AgentSelectedEvent = z.infer<typeof AgentSelectedEvent>;

export const ToolCallEvent = z.object({
  type: z.literal("tool.call"),
  run_id: z.string().optional(),
  name: z.string(),
  status: z.enum(["running", "done"]).default("running"),
});

export const ThinkingEvent = z.object({
  type: z.literal("agent.thinking"),
  run_id: z.string().optional(),
  delta: z.string().default(""),
});

export const ChoiceCardEvent = z.object({
  type: z.literal("choice.card"),
  run_id: z.string().optional(),
  card: ChoiceCard,
});

/**
 * A typed failure. The old engine logged failures and dropped them, which is how
 * the Project Manager agent stayed undispatchable without anyone noticing: the
 * turn simply produced nothing. Errors are surfaced here.
 */
export const ErrorEvent = z.object({
  type: z.literal("error"),
  message: z.string().optional(),
  detail: z.string().optional(),
  agent: OrchestratorAgentId.optional(),
});

export const OrchestratorEvent = z.discriminatedUnion("type", [
  StreamChunkEvent,
  StreamEndEvent,
  AgentSelectedEvent,
  ToolCallEvent,
  ThinkingEvent,
  ChoiceCardEvent,
  ErrorEvent,
  ...ARTIFACT_EVENTS,
]);
export type OrchestratorEvent = z.infer<typeof OrchestratorEvent>;

// ── client → server ────────────────────────────────────────────────────────

export interface OrchestratorUserMessage {
  type: "user_message";
  text: string;
  /**
   * WHICH AGENT RUNS THIS TURN — an OVERRIDE, and normally absent.
   *
   * Omitted, the Orchestrator routes: it reads the message, and the connection's
   * earlier turns, and picks one of the nine or answers directly. Present, that
   * agent runs and no routing call is made.
   *
   * The key must be OMITTED rather than sent as `null` or `""` when there is no
   * override. The backend reads `(msg.get("agent") or "").strip()`, so any falsy
   * value routes — but sending a field the protocol does not declare is how frames
   * start being dropped, and this union has already lost one event type that way.
   *
   * Phase 2 required this field and refused a frame without it, because there was
   * no router yet. The invariant that refusal stood for is unchanged and now lives
   * in the engine: nothing is chosen silently. Every routed turn announces its
   * agent AND the reason in `agent.selected`, before any of the agent's text, so a
   * wrong choice is visible and correctable in one turn — which is precisely what
   * the previous engine, advancing by list index, never told anyone.
   */
  agent?: OrchestratorAgentId;
  /**
   * A REAL `runs` row this caller's tenant owns. The socket resolves it and
   * refuses anything it cannot verify, because the id becomes the LangGraph
   * `thread_id`. A client-invented conversation key is not a run id and is
   * rejected outright.
   */
  run_id: string;
}

export interface OrchestratorChoiceAnswer {
  type: "choice_answer";
  card_id: string;
  selected_ids: string[];
  free_text?: string;
  run_id: string;
}

export type OrchestratorOutbound =
  | OrchestratorUserMessage
  | OrchestratorChoiceAnswer;
