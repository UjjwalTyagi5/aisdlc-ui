/**
 * Orchestrator WS ticket mint.
 *
 * The Orchestrator cockpit opens a BIDIRECTIONAL WebSocket to the FastAPI
 * Orchestrator endpoint (it sends user turns and receives streamed events), so —
 * unlike the one-way SSE bridges (`/api/chat`, `/api/runs/[id]/stream`) that keep
 * the WS entirely server-side — the browser needs to open the socket itself. To
 * keep the auth boundary intact we NEVER expose the BFF JWT: this route mints a
 * single-use, 20-second Redis ticket server-side (the exact same `mintWsTicket`
 * flow the SSE bridges and `/api/copilot/ws-ticket` use) and returns ONLY that
 * short-lived ticket plus the browser-reachable WS URL. FastAPI redeems the
 * ticket atomically (GETDEL) on connect.
 *
 * A ticket is NOT a licence to drive the Orchestrator. It carries only
 * `{user_id, tenant_id}`; the socket resolves the caller's platform role from
 * those claims and refuses anyone who is not a Project Admin BEFORE accepting the
 * handshake (see `agents_orchestrator/orchestrator2/ws.py`). Minting here for a
 * signed-in user is therefore correct — the access decision is the socket's, and
 * deliberately not this route's, because gating the UI is not access control.
 *
 * Response: { ticket, wsUrl } — the hook appends `?ticket=<t>`.
 */
import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { mintWsTicket, fastapiWsUrl } from "@/lib/bff/ws-ticket";

const ORCHESTRATOR_WS_PATH = "/sdlc/agent/orchestrator2/ws";

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
    // In local dev FASTAPI_INTERNAL_URL (ws://127.0.0.1:8004) is also the
    // browser-reachable host. A deployed environment fronts FastAPI with a public
    // gateway; expose it via NEXT_PUBLIC_ORCHESTRATOR_WS_BASE when that host
    // differs from the internal URL. NEXT_PUBLIC_COPILOT_WS_BASE is honoured as a
    // fallback because both sockets are served by the same FastAPI process — an
    // environment that has already published one gateway host should not have to
    // publish it twice to turn the Orchestrator on.
    const wsBase =
      process.env["NEXT_PUBLIC_ORCHESTRATOR_WS_BASE"] ??
      process.env["NEXT_PUBLIC_COPILOT_WS_BASE"] ??
      fastapiWsUrl();
    const wsUrl = `${wsBase}${ORCHESTRATOR_WS_PATH}`;
    return new Response(JSON.stringify({ ticket, wsUrl }), {
      status: 200,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch (err) {
    console.error("[orchestrator-ticket] failed to mint WS ticket:", err);
    return new Response(
      JSON.stringify({
        code: "ticket_mint_failed",
        message: "Could not open the Orchestrator session.",
      }),
      { status: 502, headers: { "Content-Type": "application/json" } },
    );
  }
}
