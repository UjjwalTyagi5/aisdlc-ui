/**
 * SSE bridge: maps the FastAPI agent WebSocket stream for a run to an
 * EventSource-compatible text/event-stream response.
 *
 * The browser holds an EventSource pointing at this route — the WS connection
 * to FastAPI is opened and managed entirely on the server side so the browser
 * never touches the WS ticket or the service JWT (T-M4-13, T-M4-14).
 *
 * Message flow:
 *   Browser EventSource → GET /api/runs/:id/stream
 *     → mintWsTicket (POST /auth/ws-ticket, server-side)
 *     → WebSocket ws://{fastapi}/sdlc/agent/orchestrator/ws?ticket=<ticket>
 *     → each WS message → mapWsToSseEvent → data: <json>\n\n
 *     → WS close → stream.closed marker (non-terminal) → SSE close
 *
 * This handler is consumed by lib/api/stream.ts subscribeToRun() and
 * lib/stream/use-sse.ts — neither of those files is modified.
 */
import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { mapWsToSseEvent } from "@/lib/bff/ws-to-sse";
import { mintWsTicket, fastapiWsUrl } from "@/lib/bff/ws-ticket";

/**
 * GET /api/runs/[id]/stream
 *
 * Returns a text/event-stream response. The client must hold this connection
 * open; the SSE bridge relays all agent WS events until the WS closes.
 */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) {
    return new Response(JSON.stringify({ code: "unauthenticated" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  const { id: runId } = await params;

  const { readable, writable } = new TransformStream<Uint8Array, Uint8Array>();
  const writer = writable.getWriter();
  const encoder = new TextEncoder();

  const writeSse = (event: unknown) => {
    writer
      .write(encoder.encode(`data: ${JSON.stringify(event)}\n\n`))
      .catch(() => {
        // Client disconnected — writer may already be closed.
      });
  };

  const closeStream = () => {
    writer.close().catch(() => {
      // Already closed — ignore.
    });
  };

  // Ticket mint + WS open are intentionally async; the readable is returned
  // immediately so the browser's EventSource sees 200 + headers right away.
  void openWsBridge(session, runId, writeSse, closeStream);

  return new Response(readable, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      // Prevent Vercel / nginx from buffering the stream.
      "X-Accel-Buffering": "no",
    },
  });
}

/** Opens the upstream WebSocket and pipes events into the SSE writer. */
async function openWsBridge(
  session: Parameters<typeof mintWsTicket>[0],
  runId: string,
  writeSse: (event: unknown) => void,
  closeStream: () => void,
): Promise<void> {
  let ws: WebSocket | null = null;
  try {
    const ticket = await mintWsTicket(session);
    const wsBase = fastapiWsUrl();
    ws = new WebSocket(
      `${wsBase}/sdlc/agent/orchestrator/ws?ticket=${encodeURIComponent(ticket)}`,
    );
  } catch (err) {
    // Ticket mint or WS constructor failure — emit terminal event and close.
    console.error("[stream-bridge] failed to open WS connection:", err);
    writeSse({ type: "run.completed", runId, status: "failed", at: new Date().toISOString() });
    closeStream();
    return;
  }

  ws.onmessage = (msg: MessageEvent) => {
    try {
      const mapped = mapWsToSseEvent(
        JSON.parse(msg.data as string),
        runId,
      );
      if (mapped) writeSse(mapped);
    } catch {
      // Malformed JSON from backend — discard silently (T-M4-14).
    }
  };

  ws.onclose = () => {
    // The WS closing is a transport event — it does NOT mean the run failed or
    // completed. The backend may close idle connections, reconnect, or the run
    // may be awaiting an approval gate. Emitting a fabricated run.completed
    // here caused recurring "failed/approved" bubbles for healthy runs.
    // Emit a non-terminal stream.closed marker so the SSE subscriber can stop
    // listening; the run-detail query drives actual terminal state display.
    writeSse({ type: "stream.closed", runId, at: new Date().toISOString() });
    closeStream();
  };

  ws.onerror = () => {
    // onclose fires after onerror for WebSocket — the close handler above
    // will emit the terminal event and close the stream (T-M4-15).
  };
}
