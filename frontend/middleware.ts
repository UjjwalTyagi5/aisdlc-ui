import { NextResponse, type NextRequest } from "next/server";

/**
 * Auth guard.
 *
 *  - mock mode → checks for `sdlc_session` cookie set by /login.
 *  - auth0 mode → delegates to the Auth0 SDK middleware, which also mounts
 *    /auth/login, /auth/logout, /auth/callback, /auth/access-token, /auth/profile.
 *
 * The auth0 module is dynamically imported so mock-mode devs don't need
 * AUTH0_* env vars just to boot.
 */
const AUTH0_MODE = process.env.NEXT_PUBLIC_AUTH_MODE === "auth0";

const PUBLIC_PREFIXES = ["/login", "/api/auth", "/_next", "/favicon.ico"];
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

  // Mock mode — simple cookie gate. Downstream components validate role via `useCan`.
  const hasSession = req.cookies.has("sdlc_session");
  if (!hasSession) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("from", pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.[\\w]+$).*)"],
};
