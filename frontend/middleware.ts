import { NextResponse, type NextRequest } from "next/server";

import { TOKEN_COOKIE_NAME, verifyBackendToken } from "@/lib/auth/token";

/**
 * Auth guard.
 *
 *  - local mode → verifies the backend-issued JWT in the `sdlc_token` cookie.
 *  - mock mode → checks for the `sdlc_session` cookie set by /login.
 *  - auth0 mode → delegates to the Auth0 SDK middleware, which also mounts
 *    /auth/login, /auth/logout, /auth/callback, /auth/access-token, /auth/profile.
 *
 * The auth0 module is dynamically imported so mock-mode devs don't need
 * AUTH0_* env vars just to boot. `verifyBackendToken` uses `jose`, which runs in
 * the Edge runtime this file executes in.
 */
const AUTH0_MODE = process.env.NEXT_PUBLIC_AUTH_MODE === "auth0";
const isLocalAuth = process.env.NEXT_PUBLIC_AUTH_MODE === "local";

// The pages a person with no session must be able to reach.
//
// `/reset-password` is load-bearing, not a convenience: an onboarded account has NO
// password until its emailed link is used, so somebody arriving from that email is
// necessarily unauthenticated. Leaving it out of this list would bounce every invite to
// /login and make onboarding impossible to complete.
//
// `/api/auth` stays public because the sign-in, forgot-password and reset proxies are all
// reached before any session exists.
const PUBLIC_PREFIXES = [
  "/login",
  "/forgot-password",
  "/reset-password",
  "/api/auth",
  "/_next",
  "/favicon.ico",
];
const AUTH0_ROUTE_PREFIX = "/auth/";

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  // Auth0 mounts its own routes — always let them through to its middleware
  if (AUTH0_MODE && pathname.startsWith(AUTH0_ROUTE_PREFIX)) {
    const { getAuth0 } = await import("@/lib/auth/auth0");
    return getAuth0().middleware(req);
  }

  // Public marketing landing lives at the root.
  if (pathname === "/") {
    return NextResponse.next();
  }

  if (PUBLIC_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    return NextResponse.next();
  }

  if (AUTH0_MODE) {
    const { getAuth0 } = await import("@/lib/auth/auth0");
    return getAuth0().middleware(req);
  }

  // Local mode — the token must VERIFY, not merely be present. A presence check
  // was the outer half of the bypass in docs/rbac-audit-2026-08-17.md: any
  // string in the cookie counted as a session, and the BFF then signed whatever
  // it decoded. Downstream components still gate on permissions; this only
  // decides whether there is an authenticated caller at all.
  if (isLocalAuth) {
    const claims = await verifyBackendToken(
      req.cookies.get(TOKEN_COOKIE_NAME)?.value,
    );
    if (!claims) return redirectToLogin(req, pathname);
    return NextResponse.next();
  }

  // Mock mode — simple cookie gate. Downstream components validate role via `useCan`.
  // Acceptable only because mock mode cannot reach a real backend: `mintBffToken`
  // throws outside it, so a forged cookie here buys nothing beyond the fixtures.
  if (!req.cookies.has("sdlc_session")) {
    return redirectToLogin(req, pathname);
  }
  return NextResponse.next();
}

function redirectToLogin(req: NextRequest, pathname: string) {
  const url = req.nextUrl.clone();
  url.pathname = "/login";
  url.searchParams.set("from", pathname);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.[\\w]+$).*)"],
};
