export type AuthMode = "mock" | "auth0" | "local";

/**
 * Exposed via NEXT_PUBLIC_ so both server and client code agree.
 * - "local"  → our own email+password auth (Phase 3). Verifies against the
 *               FastAPI POST /auth/login and stores the resolved session cookie.
 * - "auth0"  → enterprise OIDC broker (dormant; kept for future SSO).
 * - "mock"   → role-picker local session (default, so devs without any auth
 *               backend can still run the app).
 */
export const AUTH_MODE: AuthMode =
  process.env.NEXT_PUBLIC_AUTH_MODE === "auth0"
    ? "auth0"
    : process.env.NEXT_PUBLIC_AUTH_MODE === "local"
      ? "local"
      : "mock";

export const isMockAuth = AUTH_MODE === "mock";
export const isAuth0 = AUTH_MODE === "auth0";
export const isLocalAuth = AUTH_MODE === "local";

/**
 * Frontend mirror of the backend ENABLE_OIDC flag (REQ-M7-15 lockstep).
 *
 * Gates login-UI visibility only — the authoritative backend dispatch is the
 * server-only config/env.py ENABLE_OIDC (T-7.3-11 / T-7.3-13). A browser
 * cannot force this flag to weaken backend RS256 validation; the HS256 path
 * still requires the server-only JWT_SECRET_KEY.
 */
export const isOidcEnabled =
  process.env.NEXT_PUBLIC_ENABLE_OIDC === "true";
