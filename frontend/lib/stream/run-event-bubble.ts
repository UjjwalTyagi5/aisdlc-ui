import type { StreamEvent } from "@/lib/schemas";

/**
 * Shared mapping from a per-run SSE `StreamEvent` to a chat-bubble shape.
 * Lifted out of `RunStreamFeed`'s inline switch (stream-subscriber.tsx) so
 * both the compact drawer feed and the full Run Conversation surface render
 * the same events consistently.
 *
 * Returns `null` for events that shouldn't surface as a bubble (e.g. the
 * bare `run.started` ack).
 */
export type BubbleTone = "brand" | "info" | "success" | "warning" | "danger";

/** Who the bubble is attributed to — drives alignment + avatar styling. */
export type BubbleRole = "agent" | "orchestrator" | "system";

export interface RunBubble {
  /** Stable-ish key for React (event type + timestamp). */
  id: string;
  at: string;
  role: BubbleRole;
  tone: BubbleTone;
  /** Short label shown above/beside the bubble body. */
  label: string;
  /** Plain-text body. Rendered as text — never as HTML. */
  text: string;
}

export function bubbleFromRunEvent(ev: StreamEvent): RunBubble | null {
  const base = { at: ev.at, id: `${ev.type}-${ev.at}` };

  switch (ev.type) {
    case "step.progress":
      return {
        ...base,
        role: "agent",
        tone: "brand",
        label: ev.title,
        text: ev.status,
      };
    case "step.output.delta":
      return {
        ...base,
        role: "agent",
        tone: "info",
        label: "Output",
        text: ev.delta,
      };
    case "artifact.updated":
      return {
        ...base,
        role: "agent",
        tone: "success",
        label: ev.name ? `Artifact · ${ev.name}` : "Artifact updated",
        text: ev.status,
      };
    case "cost.update":
      return {
        ...base,
        role: "system",
        tone: "info",
        label: "Cost",
        text: `$${ev.cost.usd.toFixed(4)} · ${ev.cost.inputTokens + ev.cost.outputTokens} tok`,
      };
    case "guardrail.hit":
      return {
        ...base,
        role: "system",
        tone: ev.blocked ? "danger" : "warning",
        label: ev.blocked ? `Guardrail blocked · ${ev.kind}` : `Guardrail · ${ev.kind}`,
        text: ev.message,
      };
    case "hitl.pending":
      return {
        ...base,
        role: "orchestrator",
        tone: "warning",
        label: "Approval gate",
        text: ev.title,
      };
    case "run.completed":
      return {
        ...base,
        role: "orchestrator",
        tone: ev.status === "failed" || ev.status === "rejected" ? "danger" : "success",
        label: "Run completed",
        text: ev.status,
      };
    case "run.started":
      return null;
    default:
      return null;
  }
}
