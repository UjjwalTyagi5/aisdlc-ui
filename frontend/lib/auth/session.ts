import { cookies } from "next/headers";

import { fetchPermissions } from "@/lib/api/auth";

import { deriveRole } from "./local";
import { isLocalAuth, isMockAuth } from "./mode";
import { MOCK_COOKIE_NAME, decodeSession } from "./mock";
import { TOKEN_COOKIE_NAME, verifyBackendToken } from "./token";
import type { Role, Session } from "./types";

/**
 * Server-only — reads the current session from the backend token (local mode),
 * the mock cookie (mock mode), or Auth0's session store. Returns `null` when
 * not authenticated.
 *
 * Use this in server components + route handlers, then pass the result
 * to `<SessionProvider value={session}>` so client hooks can read it.
 */
export async function getSession(): Promise<Session | null> {
  if (isMockAuth) {
    // Mock mode has no backend to issue anything, so the cookie IS the session.
    // Safe only because mock mode cannot reach a real backend: `bearerForRequest`
    // refuses to mint outside it (lib/bff/client.ts).
    const store = await cookies();
    return decodeSession(store.get(MOCK_COOKIE_NAME)?.value);
  }

  if (isLocalAuth) {
    // THE TOKEN IS THE AUTHORITY, NOT THE COOKIE. `sdlc_session` carries display
    // fields — a name, an email, an organization label — whose worst case is a
    // wrong avatar. Identity, tenant and permissions are read from the
    // signature-verified token and overwrite whatever the display cookie says,
    // because that cookie is unsigned and was, until this change, the whole
    // basis of the app's authorization. See docs/rbac-audit-2026-08-17.md.
    const store = await cookies();
    const claims = await verifyBackendToken(store.get(TOKEN_COOKIE_NAME)?.value);
    if (!claims) return null;

    const display = decodeSession(store.get(MOCK_COOKIE_NAME)?.value);
    return {
      user: {
        id: claims.sub,
        name: display?.user.name ?? claims.sub,
        email: display?.user.email ?? "",
        initials: display?.user.initials ?? "?",
      },
      tenant: {
        id: claims.tenant_id,
        name: display?.tenant.name ?? "Organization",
        plan: display?.tenant.plan ?? "Enterprise",
      },
      // Derived from the token's permissions, NOT read from the display cookie.
      // The coarse role still drives the legacy `useCan`/capability gates and
      // `canCreateProject`, so letting an unsigned cookie set it would leave a
      // smaller version of the hole this change closes.
      role: deriveRole(claims.permissions),
      mode: "local",
      tier: display?.tier ?? "org",
      permissions: claims.permissions,
    };
  }

  const { getAuth0 } = await import("./auth0");
  const a0 = await getAuth0().getSession();
  if (!a0?.user) return null;

  // Map Auth0 claims → our canonical Session. Roles and tenant come from
  // custom claims set by an Auth0 Action (tenant isolation + RBAC).
  const role: Role =
    (a0.user["https://sdlc/role"] as Role | undefined) ?? "viewer";
  const tenantClaim = a0.user["https://sdlc/tenant"] as
    | { id: string; name: string; plan?: string }
    | undefined;

  // Build the BASE session first — its `permissions` is legitimately `[]` at
  // this point (D-09/IDENTITY-TOKEN SEAM, milestone-7.2 plan 05). This is the
  // SAME object passed straight into fetchPermissions()/bffFetch/mintBffToken
  // below: there is no lower-level sub+tenant minter, and constructing a
  // synthetic/placeholder Session would be the wrong move. The bootstrap call
  // to GET /auth/permissions is public()-marked (NOT permission-gated) and
  // derives identity from request.state.{user_id,tenant_id} — set by FastAPI's
  // verified-JWT middleware, NOT from the (still-empty) permissions claim — so
  // minting a token from the base session here is correct and harmless.
  const baseSession: Session = {
    user: {
      id: String(a0.user.sub ?? ""),
      name: String(a0.user.name ?? a0.user.email ?? ""),
      email: String(a0.user.email ?? ""),
      avatarUrl: a0.user.picture ? String(a0.user.picture) : undefined,
      initials: initialsFrom(String(a0.user.name ?? a0.user.email ?? "?")),
    },
    tenant: {
      id: tenantClaim?.id ?? "ws_default",
      name: tenantClaim?.name ?? "Default workspace",
      plan: tenantClaim?.plan ?? "Trial",
    },
    role,
    mode: "auth0",
    permissions: [],
  };

  // Enrich with the DB-resolved effective permission set (REQ-M7-12 frontend
  // half). fetchPermissions fails closed to [] on any error — a backend
  // hiccup never silently grants phantom access (T-7.2-26).
  const permissions = await fetchPermissions(baseSession);

  return { ...baseSession, permissions };
}

function initialsFrom(name: string): string {
  const parts = name.trim().split(/\s+/).slice(0, 2);
  return parts.map((p) => p[0]?.toUpperCase() ?? "").join("") || "?";
}
