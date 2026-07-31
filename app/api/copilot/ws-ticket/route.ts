/**
 * Copilot WS ticket mint.
 *
 * The Copilot page opens a BIDIRECTIONAL WebSocket to the FastAPI Copilot
 * endpoint (it both sends user turns / choice answers and receives streamed
 * events), so — unlike the one-way SSE bridges (`/api/chat`, `/api/runs/[id]/stream`)
 * that keep the WS entirely server-side — the browser needs to open the socket
 * itself. To keep the auth boundary intact we NEVER expose the BFF JWT: this
 * route mints a single-use, 20-second Redis ticket server-side (the exact same
 * `mintWsTicket` flow the SSE bridges use) and returns ONLY that short-lived
 * ticket plus the browser-reachable WS URL. FastAPI redeems the ticket
 * atomically (GETDEL) on connect.
 *
 * Response: { ticket, wsUrl } — the hook appends `?ticket=<t>&run=<runId>`.
 */
import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { mintWsTicket, fastapiWsUrl } from "@/lib/bff/ws-ticket";

const COPILOT_WS_PATH = "/sdlc/agent/copilot/ws";

export async function POST(_req: NextRequest) {
  const session = await getSession();
  if (!session) {
    return new Response(JSON.stringify({ code: "unauthenticated" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  try {
    const ticket = await mintWsTicket(session);
    // In local dev FASTAPI_INTERNAL_URL (ws://127.0.0.1:8001) is also the
    // browser-reachable host. A deployed environment fronts FastAPI with a
    // public gateway; expose it via NEXT_PUBLIC_COPILOT_WS_BASE when that host
    // differs from the internal URL.
    const wsBase = process.env["NEXT_PUBLIC_COPILOT_WS_BASE"] ?? fastapiWsUrl();
    const wsUrl = `${wsBase}${COPILOT_WS_PATH}`;
    return new Response(JSON.stringify({ ticket, wsUrl }), {
      status: 200,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch (err) {
    console.error("[copilot-ticket] failed to mint WS ticket:", err);
    return new Response(
      JSON.stringify({ code: "ticket_mint_failed", message: "Could not open the Copilot session." }),
      { status: 502, headers: { "Content-Type": "application/json" } },
    );
  }
}
