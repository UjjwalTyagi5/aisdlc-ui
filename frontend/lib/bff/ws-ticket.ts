/**
 * Server-only WS ticket minter.
 *
 * The browser NEVER touches the WS ticket — it is minted and redeemed
 * entirely on the server side (BFF→FastAPI), keeping the 20-second TTL
 * window well within the BFF request-open-WS roundtrip time (T-M4-13).
 *
 * Flow:
 *   1. BFF calls POST {FASTAPI_INTERNAL_URL}/auth/ws-ticket with
 *      Authorization: Bearer <hs256-bff-jwt>
 *   2. FastAPI validates the JWT and mints a single-use Redis ticket
 *      (Redis SETEX 20s TTL).
 *   3. BFF receives {ticket, ttl_seconds} and immediately opens the WS
 *      connection: wss://{host}/sdlc/agent/orchestrator/ws?ticket=<ticket>
 *   4. FastAPI redeems the ticket via Redis GETDEL (atomic, single-use).
 */

import type { Session } from "@/lib/auth/types";
import { bearerForRequest } from "@/lib/bff/client";

const FASTAPI_BASE =
  process.env["FASTAPI_INTERNAL_URL"] ?? "http://localhost:8001";

/** Mints a single-use WebSocket ticket from the FastAPI ticket endpoint. */
export async function mintWsTicket(session: Session): Promise<string> {
  // Same three-way branch every other BFF->FastAPI call already uses
  // (lib/bff/client.ts::bearerForRequest) -- OIDC forwards the Auth0 token,
  // local mode forwards the token FastAPI itself issued at login, everything
  // else mints. This used to call mintBffToken() directly, which throws in
  // local mode (by design -- see lib/bff/jwt.ts) since local sessions carry
  // client-writable state that must never be signed into a trusted claim.
  // That made the WS chat bridge the one BFF surface never updated to the
  // post-audit pattern every REST proxy route already uses -- unnoticed until
  // now because every prior chat-access test exercised the backend WS handler
  // directly, never through this Next.js bridge.
  const jwt = await bearerForRequest(session);
  const res = await fetch(`${FASTAPI_BASE}/auth/ws-ticket`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${jwt}`,
    },
  });
  if (!res.ok) {
    throw new Error(
      `Failed to mint WS ticket: ${res.status} ${res.statusText}`,
    );
  }
  const body = (await res.json()) as { ticket: string; ttl_seconds: number };
  return body.ticket;
}

/**
 * Derives the FastAPI WebSocket base URL from the HTTP internal URL.
 * e.g. http://localhost:8001 → ws://localhost:8001
 *      https://api.example.com → wss://api.example.com
 */
export function fastapiWsUrl(): string {
  return FASTAPI_BASE.replace(/^https:\/\//, "wss://").replace(
    /^http:\/\//,
    "ws://",
  );
}
