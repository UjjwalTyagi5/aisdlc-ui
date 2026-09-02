/**
 * WS→SSE event mapper.
 *
 * Translates raw FastAPI agent WebSocket messages into valid Zod StreamEvent
 * objects suitable for the browser's SSE consumer (lib/api/stream.ts,
 * lib/stream/use-sse.ts). The browser NEVER receives raw WS messages —
 * only schema-valid StreamEvents that pass StreamEvent.safeParse().
 *
 * Mapping table (RESEARCH §4):
 *
 *   stream_chunk        → step.output.delta  (delta=content, synthetic stepId)
 *   stream_end          → run.completed      (status="approved")
 *   file_generated      → artifact.updated   (status="approved", synthetic artifactId)
 *   file_diff           → code.diff         (path/original/modified/changeKind passthrough)
 *   agent_completed     → run.completed      (status="failed"  — only when success=false)
 *   activity_update     → run.completed      (status="approved" — only when activity.type="complete")
 *   message_received    → null (discard — ack only, no UI value)
 *   echo                → null (discard — unrecognized type echo)
 *   everything else     → null (discard)
 *
 * Security note (T-M4-14): returning null for unrecognised types prevents any
 * backend-side injection of arbitrary payloads into the SSE stream.
 */

import { StreamEvent } from "@/lib/schemas/stream";

/** Raw FastAPI WebSocket message — typed loosely; validated on the way out. */
type WsMessage = Record<string, unknown>;

/**
 * Maps a raw FastAPI agent WebSocket message to a Zod-valid StreamEvent,
 * or returns null if the message type has no UI representation and must
 * be discarded.
 *
 * @param wsMsg  - The parsed JSON payload received from the agent WebSocket.
 * @param runId  - The run ID in the current BFF bridge session context.
 * @returns A StreamEvent that passes StreamEvent.safeParse(), or null.
 */
export function mapWsToSseEvent(wsMsg: unknown, runId: string): StreamEvent | null {
  if (!isWsMessage(wsMsg)) return null;

  const now = new Date().toISOString();

  switch (wsMsg.type) {
    case "stream_chunk": {
      // Deterministic synthetic stepId — stable across every token in the same run.
      const stepId = `${runId}:agent`;
      const event = {
        type: "step.output.delta" as const,
        runId,
        stepId,
        delta: typeof wsMsg.content === "string" ? wsMsg.content : "",
        at: now,
      };
      return validateOrNull(event);
    }

    case "agent_response": {
      // Testing agent (and other request/response agents) emit a single
      // `agent_response` with the full reply instead of token chunks. Surface it
      // as one delta on the same synthetic step; the terminal run.completed comes
      // from the handler's activity_update(complete).
      if (wsMsg.agent_name === "Error Agent" && typeof wsMsg.message !== "string") return null;
      const event = {
        type: "step.output.delta" as const,
        runId,
        stepId: `${runId}:agent`,
        delta: typeof wsMsg.message === "string" ? wsMsg.message : "",
        at: now,
      };
      return validateOrNull(event);
    }

    case "stream_end": {
      const event = {
        type: "run.completed" as const,
        runId,
        status: "approved" as const,
        at: now,
      };
      return validateOrNull(event);
    }

    case "file_generated": {
      // Deterministic synthetic artifactId derived from filename so the same
      // file always maps to the same artifact in the UI.
      const filename =
        typeof wsMsg.filename === "string" ? wsMsg.filename : "unknown";
      const artifactId = `artifact:${runId}:${filename}`;
      const event = {
        type: "artifact.updated" as const,
        runId,
        artifactId,
        status: "approved" as const,
        name: filename,
        url: typeof wsMsg.url === "string" ? wsMsg.url : undefined,
        at: now,
      };
      return validateOrNull(event);
    }

    case "file_diff": {
      const changeKind =
        wsMsg.change_kind === "created" || wsMsg.change_kind === "edited"
          ? wsMsg.change_kind
          : undefined;
      const event = {
        type: "code.diff" as const,
        runId,
        path: typeof wsMsg.path === "string" ? wsMsg.path : "",
        original: typeof wsMsg.original === "string" ? wsMsg.original : "",
        modified: typeof wsMsg.modified === "string" ? wsMsg.modified : "",
        changeKind,
        at: now,
      };
      return validateOrNull(event);
    }

    case "agent_completed": {
      // Only the failure case is a terminal signal for the UI.
      // success=true means the agent finished normally; stream_end carries
      // the authoritative completion signal in that path.
      if (wsMsg.success !== false) return null;
      const event = {
        type: "run.completed" as const,
        runId,
        status: "failed" as const,
        at: now,
      };
      return validateOrNull(event);
    }

    case "activity_update": {
      // Only the "complete" activity type maps to a terminal UI signal.
      const activity = wsMsg.activity;
      if (
        activity !== null &&
        typeof activity === "object" &&
        (activity as Record<string, unknown>).type === "complete"
      ) {
        const event = {
          type: "run.completed" as const,
          runId,
          status: "approved" as const,
          at: now,
        };
        return validateOrNull(event);
      }
      // Other activity types (in_progress, error, etc.) have insufficient
      // fidelity to map to a meaningful StreamEvent — discard.
      return null;
    }

    // Ack-only or echo types — discard; no UI representation.
    case "message_received":
    case "echo":
    default:
      return null;
  }
}

/** Runs the Zod parse and returns the typed value, or null on failure. */
function validateOrNull(candidate: unknown): StreamEvent | null {
  const parsed = StreamEvent.safeParse(candidate);
  if (!parsed.success) {
    // Should never happen if the mapping table is correct, but we fail
    // closed rather than forwarding an invalid event to the client.
    console.warn("[ws-to-sse] mapped event failed StreamEvent.safeParse", parsed.error.issues);
    return null;
  }
  return parsed.data;
}

/** Type guard: input must be a non-null object with a string `type` field. */
function isWsMessage(value: unknown): value is WsMessage {
  return (
    value !== null &&
    typeof value === "object" &&
    "type" in (value as object) &&
    typeof (value as WsMessage).type === "string"
  );
}
