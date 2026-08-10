/**
 * Server-only BFF JWT minter.
 *
 * Mints a short-lived HS256 JWT for server-to-server calls from the Next.js
 * BFF to the FastAPI resource API. The browser never receives this token —
 * it is created and consumed entirely on the server side.
 *
 * Claims match the FastAPI `create_access_token` contract exactly:
 *   { sub, tenant_id, permissions, exp }
 *
 * `permissions` (REQ-M7-12, milestone-7.2 plan 05): baked in so the BFF's OWN
 * write calls satisfy live `require_permission` gates — enrichment of
 * Session.permissions alone would leave the frontend able to render an action
 * it could never actually perform. The BFF only ever signs permissions it
 * sourced server-side from `getSession()` (mock-seeded or
 * `/auth/permissions`-resolved); the browser cannot inject this claim
 * (T-7.2-23, mitigated — token minted server-only, secret never NEXT_PUBLIC_).
 *
 * Security properties:
 * - Fails closed: throws if JWT_SECRET_KEY is unset (no silent empty-secret signing).
 * - Server-only: JWT_SECRET_KEY must NEVER carry a NEXT_PUBLIC_ prefix.
 * - Short-lived: exp = now + 60 min (T-M4-09).
 */
import { SignJWT } from "jose";

import type { Session } from "@/lib/auth/types";

const TOKEN_TTL_SECONDS = 60 * 60; // 60 minutes (T-M4-09)

/**
 * Mints an HS256 JWT from the authenticated session for use in
 * `Authorization: Bearer` headers on BFF → FastAPI calls.
 *
 * @param session - The current authenticated session (server-side only).
 * @returns A signed compact JWT string.
 * @throws {Error} When JWT_SECRET_KEY environment variable is not set.
 */
export async function mintBffToken(session: Session): Promise<string> {
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
