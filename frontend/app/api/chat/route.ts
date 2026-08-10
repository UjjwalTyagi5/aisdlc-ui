/**
 * Chat SSE bridge: accepts a POST with a user message, opens the orchestrator
 * WebSocket, forwards the message, and streams the agent reply back as SSE.
 *
 * Message flow:
 *   Browser POST /api/chat { message, sessionId?, context? }
 *     → mintWsTicket (server-side)
 *     → WebSocket ws://{fastapi}/sdlc/agent/orchestrator/ws?ticket=<ticket>
 *     → send user_message_with_files payload
 *     → each WS message → mapWsToSseEvent → data: <json>\n\n
 *     → WS close → terminal run.completed event → SSE close
 *
 * Raw WS messages are NEVER forwarded to the client — every outbound SSE
 * event must pass through mapWsToSseEvent (T-M4-14).
 */
import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { mapWsToSseEvent } from "@/lib/bff/ws-to-sse";
import { mintWsTicket, fastapiWsUrl } from "@/lib/bff/ws-ticket";

/** Minimal chat request body. */
interface ChatRequest {
  message: string;
  sessionId?: string;
  context?: unknown;
  /**
   * Target agent for a self-contained agent page (e.g. "requirement" →
   * /sdlc/agent/requirement/ws). Omitted → the orchestrator WS. Routing an
   * agent page's chat directly to its agent is what lets the page's
   * pipeline_context (e.g. selected story refs) reach the agent; the
   * orchestrator rebuilds context from persisted artifacts and would drop it.
   */
  agent?: string;
  /**
   * Extra top-level fields forwarded verbatim into the agent WS payload — used by
   * agents that take structured run parameters (e.g. the Testing agent's
   * selected_test_types / target_url / test_config / clone_target).
   */
  agentParams?: Record<string, unknown>;
}

/** Map an agent id to its FastAPI WS path. Unknown/absent → orchestrator. */
function agentWsPath(agent?: string): string {
  switch (agent) {
    case "requirement":
    case "requirements":
      return "/sdlc/agent/requirement/ws";
    case "design":
      return "/sdlc/agent/design/ws";
    case "development":
      return "/sdlc/agent/development/ws";
    case "code_review":
    case "code-review":
      return "/sdlc/agent/code-review/ws";
    case "security":
      return "/sdlc/agent/security/ws";
    case "testing":
      return "/sdlc/agent/testing/ws";
    case "deployment":
      return "/sdlc/agent/deployment/ws";
    case "documentation":
      return "/sdlc/agent/documentation/ws";
    // Any other agent falls through to the orchestrator (functional, non-breaking).
    default:
      return "/sdlc/agent/orchestrator/ws";
  }
}

/**
 * POST /api/chat
 *
 * Returns a text/event-stream response streaming the orchestrator's reply
 * as mapped StreamEvents.
 */
export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) {
    return new Response(JSON.stringify({ code: "unauthenticated" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  let body: ChatRequest;
  try {
    body = (await req.json()) as ChatRequest;
  } catch {
    return new Response(JSON.stringify({ code: "invalid_body" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const { message, sessionId, context, agentParams } = body;
  const wsPath = agentWsPath(body.agent);

  // Use the sessionId from the request as the synthetic runId for this chat
  // session — it lets the client correlate SSE events with the conversation.
  // Underscore (not ":") — the backend agents use the session id in filesystem
  // paths and Windows forbids ":" in directory names.
  const runId = sessionId ?? `chat_${Date.now()}`;

  const { readable, writable } = new TransformStream<Uint8Array, Uint8Array>();
  const writer = writable.getWriter();
  const encoder = new TextEncoder();

  const writeSse = (event: unknown) => {
    writer
      .write(encoder.encode(`data: ${JSON.stringify(event)}\n\n`))
      .catch(() => {
        // Client disconnected.
      });
  };

  const closeStream = () => {
    writer.close().catch(() => {
      // Already closed.
    });
  };

  void openChatWsBridge(session, runId, message, context, wsPath, writeSse, closeStream, agentParams);

  return new Response(readable, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}

/** Opens the orchestrator WS, sends the chat message, pipes replies as SSE. */
async function openChatWsBridge(
  session: Parameters<typeof mintWsTicket>[0],
  runId: string,
  message: string,
  context: unknown,
  wsPath: string,
  writeSse: (event: unknown) => void,
  closeStream: () => void,
  agentParams?: Record<string, unknown>,
): Promise<void> {
  let ws: WebSocket | null = null;
  try {
    const ticket = await mintWsTicket(session);
    const wsBase = fastapiWsUrl();
    ws = new WebSocket(
      `${wsBase}${wsPath}?ticket=${encodeURIComponent(ticket)}`,
    );
  } catch (err) {
    console.error("[chat-bridge] failed to open WS connection:", err);
    writeSse({ type: "run.completed", runId, status: "failed", at: new Date().toISOString() });
    closeStream();
    return;
  }

  // The agent WS is persistent (it stays open for further turns and never fires
  // onclose after one reply). So we must end THIS request's SSE stream when the
  // terminal run.completed event arrives — otherwise the client's fetch stream
  // never ends and the chat stays "busy" (input disabled for the next turn).
  let done = false;
  // Safety net: a standalone agent turn's TRUE terminal is activity_update{complete}
  // (emitted after stream_end + post-processing). If that never arrives, close the
  // stream after this much idle time following the first stream_end so the client
  // can't hang forever. Generous so it never fires mid-turn.
  const IDLE_FALLBACK_MS = 45_000;
  let sawStreamEnd = false;
  let idleTimer: ReturnType<typeof setTimeout> | null = null;
  const clearIdle = () => {
    if (idleTimer) {
      clearTimeout(idleTimer);
      idleTimer = null;
    }
  };
  const finish = (status: "approved" | "failed") => {
    if (done) return;
    done = true;
    clearIdle();
    writeSse({ type: "run.completed", runId, status, at: new Date().toISOString() });
    try {
      ws?.close();
    } catch {
      // Already closing.
    }
    closeStream();
  };
  const armIdleClose = () => {
    clearIdle();
    idleTimer = setTimeout(() => finish("approved"), IDLE_FALLBACK_MS);
  };

  ws.onopen = () => {
    // Send the chat message once the WS handshake completes.
    const payload = JSON.stringify({
      type: "user_message_with_files",
      session_id: runId,
      task_intent: message,
      text: message,
      conversation_context: "",
      pipeline_context: context ?? null,
      files: [],
      // Structured run params for agents that consume them (Testing). Spread last
      // so they appear as top-level WS fields the agent reads directly.
      ...(agentParams ?? {}),
    });
    ws!.send(payload);
  };

  ws.onmessage = (msg: MessageEvent) => {
    let raw: Record<string, unknown>;
    try {
      raw = JSON.parse(msg.data as string) as Record<string, unknown>;
    } catch {
      return; // Malformed JSON — discard (T-M4-14).
    }
    const rawType = raw.type;

    // TRUE terminal: standalone agents emit activity_update{type:"complete"} as the
    // very last event of a turn (dev/code-review/security), AFTER stream_end and any
    // post-processing (pr_created, artifacts). Close on this, not on stream_end.
    if (
      rawType === "activity_update" &&
      raw.activity !== null &&
      typeof raw.activity === "object" &&
      (raw.activity as Record<string, unknown>).type === "complete"
    ) {
      finish("approved");
      return;
    }
    // Failure terminal.
    if (rawType === "agent_completed" && raw.success === false) {
      finish("failed");
      return;
    }
    // stream_end is a SEGMENT boundary, NOT the end of the turn — the agent keeps
    // sending after it. Do NOT close or forward a run.completed here; just arm the
    // idle safety fallback in case a true terminal never arrives.
    if (rawType === "stream_end") {
      sawStreamEnd = true;
      armIdleClose();
      return;
    }

    // Content / other events → forward via the mapper. Never forward a run.completed
    // synthesised from a non-terminal event. Each event past a stream_end resets the
    // idle fallback (more content is still coming).
    try {
      const mapped = mapWsToSseEvent(raw, runId) as { type?: string } | null;
      if (mapped && mapped.type !== "run.completed") writeSse(mapped);
    } catch {
      // Mapper failure — discard.
    }
    if (sawStreamEnd) armIdleClose();
  };

  ws.onclose = () => {
    finish("approved");
  };

  ws.onerror = () => {
    // onclose fires after onerror — handled above (T-M4-15).
  };
}
