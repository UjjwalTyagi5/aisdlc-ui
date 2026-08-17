/**
 * Server-only BFF JWT minter — MOCK MODE ONLY.
 *
 * Mints a short-lived HS256 JWT for server-to-server calls from the Next.js
 * BFF to the FastAPI resource API. The browser never receives this token —
 * it is created and consumed entirely on the server side.
 *
 * Claims match the FastAPI `create_access_token` contract exactly:
 *   { sub, tenant_id, permissions, exp }
 *
 * WHY THIS IS NOW FORBIDDEN IN LOCAL MODE. This minter used to run on the local
 * (email+password) path, signing `session.permissions` straight out of an unsigned,
 * non-httpOnly cookie into the token FastAPI trusts verbatim. Anyone who edited
 * that cookie to `["admin:*"]` got a correctly-signed organization admin token for
 * any tenant they could name — the entire backend RBAC layer, bypassed at the seam.
 * The local path now forwards the backend's own token (`lib/auth/token.ts`).
 * See finding 1 in `docs/rbac-audit-2026-08-17.md`.
 *
 * THE OTHER TWO MODES KEEP IT, and the distinction is where the session comes from,
 * not which mode is "real". In mock mode the session is seeded server-side from a
 * role picker and there is no backend to have issued a token. In auth0 mode with
 * ENABLE_OIDC=false — the documented SC#4 rollback — the session is built from a
 * verified Auth0 session and permissions resolved server-side from the backend's own
 * resolver. Neither takes a permission set from anything the client can write, which
 * is exactly what the local path did.
 *
 * The mode guard below is deliberately a throw rather than a silent no-op: a
 * misconfiguration that routes local mode back through here must be a crash, not a
 * quiet downgrade to forgeable auth.
 *
 * Security properties:
 * - Fails closed: throws if JWT_SECRET_KEY is unset (no silent empty-secret signing).
 * - Fails closed: throws in local mode.
 * - Server-only: JWT_SECRET_KEY must NEVER carry a NEXT_PUBLIC_ prefix.
 * - Short-lived: exp = now + 60 min (T-M4-09).
 */
import { SignJWT } from "jose";

import type { Session } from "@/lib/auth/types";
import { isLocalAuth } from "@/lib/auth/mode";

const TOKEN_TTL_SECONDS = 60 * 60; // 60 minutes (T-M4-09)

/**
 * Mints an HS256 JWT from a server-derived session for use in
 * `Authorization: Bearer` headers on BFF → FastAPI calls.
 *
 * @param session - The current session, built server-side (never from a cookie
 *                  the client can write).
 * @returns A signed compact JWT string.
 * @throws {Error} In local mode, or when JWT_SECRET_KEY is not set.
 */
export async function mintBffToken(session: Session): Promise<string> {
  if (isLocalAuth) {
    throw new Error(
      "mintBffToken is not permitted in local mode. The bearer there is the " +
        "token FastAPI issued at login — minting one from the session would let " +
        "a client-supplied permission set become a signed claim.",
    );
  }

  const secret = process.env["JWT_SECRET_KEY"];
  if (!secret) {
    throw new Error(
      "JWT_SECRET_KEY environment variable is not set. " +
        "Set it in .env.local (server-only, no NEXT_PUBLIC_ prefix).",
    );
  }

  const encodedSecret = new TextEncoder().encode(secret);
  const now = Math.floor(Date.now() / 1000);

  return new SignJWT({
    tenant_id: session.tenant.id,
    permissions: session.permissions ?? [],
  })
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(session.user.id)
    .setIssuedAt(now)
    .setExpirationTime(now + TOKEN_TTL_SECONDS)
    .sign(encodedSecret);
}
