/**
 * The backend-issued access token, and the cookie that holds it.
 *
 * WHY THIS MODULE EXISTS. `POST /auth/login` has always returned a signed JWT
 * (`LoginResponse.token`), and the frontend used to throw it away: it stored an
 * *unsigned* base64 session blob in a non-httpOnly cookie, and `lib/bff/jwt.ts`
 * re-signed that blob's `permissions` array into the token FastAPI trusts
 * (`process_api.py` reads `claims["permissions"]` verbatim on the HS256 path).
 * Editing one cookie therefore granted `admin:*` over any tenant whose id you
 * knew — the whole backend RBAC layer was decorative. See finding 1 in
 * `docs/rbac-audit-2026-08-17.md`.
 *
 * The rule this module establishes: **the backend is the only issuer.** Identity,
 * tenant and permissions come from a signature-verified claim and from nowhere
 * else. A display cookie may still carry a name and an email — cosmetic fields
 * whose worst case is a wrong avatar — but it can no longer say who you are or
 * what you may do.
 *
 * Server-only. `JWT_SECRET_KEY` must never carry a NEXT_PUBLIC_ prefix.
 */
import { jwtVerify } from "jose";

/** Holds the backend-issued JWT. httpOnly — nothing in the browser reads it. */
export const TOKEN_COOKIE_NAME = "sdlc_token";

/**
 * The claims FastAPI mints at login. Mirrors `create_access_token` in
 * `backend/config/auth/jwt.py` and what `process_api.py:765-777` reads back.
 */
export interface BackendTokenClaims {
  sub: string;
  tenant_id: string;
  permissions: string[];
  exp: number;
}

function secretKey(): Uint8Array {
  const secret = process.env["JWT_SECRET_KEY"];
  if (!secret) {
    throw new Error(
      "JWT_SECRET_KEY environment variable is not set. " +
        "Set it in .env.local (server-only, no NEXT_PUBLIC_ prefix).",
    );
  }
  return new TextEncoder().encode(secret);
}

/**
 * Verify a backend token and return its claims, or `null` if it is absent,
 * unsigned, signed with the wrong key, expired, or malformed.
 *
 * ONE FAILURE VALUE, deliberately — mirroring `can_perform`'s reasoning. A
 * function that throws on "expired" and returns null on "missing" invites the
 * caller to treat one as an error case and fall through to the other. Every
 * failure here means the same thing: this request has no established identity.
 *
 * Runs in both the Node and Edge runtimes (`middleware.ts` needs the latter),
 * which is why it uses `jose` rather than a Node-only verifier.
 */
export async function verifyBackendToken(
  token: string | undefined | null,
): Promise<BackendTokenClaims | null> {
  if (!token) return null;
  try {
    const { payload } = await jwtVerify(token, secretKey(), {
      algorithms: ["HS256"],
    });
    const sub = typeof payload.sub === "string" ? payload.sub : "";
    const tenantId =
      typeof payload["tenant_id"] === "string" ? payload["tenant_id"] : "";
    if (!sub) return null;
    const rawPerms = payload["permissions"];
    return {
      sub,
      tenant_id: tenantId,
      // An absent or non-array claim becomes [] rather than a thrown error:
      // a token that establishes identity but grants nothing is a real state
      // (a freshly registered account has exactly this shape).
      permissions: Array.isArray(rawPerms)
        ? rawPerms.filter((p): p is string => typeof p === "string")
        : [],
      exp: typeof payload.exp === "number" ? payload.exp : 0,
    };
  } catch {
    return null;
  }
}

/**
 * Cookie options for the token, with `maxAge` taken from the token's own `exp`.
 *
 * The lifetime belongs to the token, not to a constant here. The old session
 * cookie hardcoded eight hours regardless of what the backend had decided, so
 * shortening the backend's TTL changed nothing — and `org_settings
 * .session_timeout_minutes` had no way to reach the browser at all.
 */
export function tokenCookieOptions(claims: BackendTokenClaims) {
  const remaining = claims.exp - Math.floor(Date.now() / 1000);
  return {
    httpOnly: true,
    path: "/",
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
    // Floor of 60s so a nearly-expired token still round-trips once rather than
    // being dropped by the browser and presenting as a silent failed login.
    maxAge: Math.max(remaining, 60),
  };
}
