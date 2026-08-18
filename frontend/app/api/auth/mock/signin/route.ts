import { NextResponse } from "next/server";

import {
  MOCK_COOKIE_MAX_AGE,
  MOCK_COOKIE_NAME,
  buildMockSession,
  buildMockSessionForPlatformRole,
  encodeSession,
} from "@/lib/auth/mock";
import { isMockAuth } from "@/lib/auth/mode";
import type { Role } from "@/lib/auth/types";
import { ROLE_META, type PlatformRole } from "@/lib/roles";

/**
 * WHY THIS COOKIE STAYS READABLE while the local-auth one became httpOnly.
 *
 * MSW handlers run in a Service Worker, where the Fetch spec strips the `Cookie`
 * header; MSW works around it by parsing `document.cookie` on the page side
 * (see mocks/handlers.ts). An httpOnly cookie is invisible to that, so mock mode
 * would stop resolving a session at all.
 *
 * Safe because mock mode is now non-authoritative by construction: `mintBffToken`
 * throws outside it, and `getSession()` only trusts a decoded cookie when
 * `isMockAuth`. Forging this cookie buys fixtures, not a backend token — which
 * was exactly the difference that did not hold before
 * docs/rbac-audit-2026-08-17.md.
 */
const MOCK_COOKIE_OPTIONS = {
  httpOnly: false,
  path: "/",
  sameSite: "lax",
  secure: process.env.NODE_ENV === "production",
  maxAge: MOCK_COOKIE_MAX_AGE,
} as const;

const ALLOWED: readonly Role[] = ["admin", "member", "viewer"];

function isPlatformRole(value: string): value is PlatformRole {
  return Object.prototype.hasOwnProperty.call(ROLE_META, value);
}

export async function POST(req: Request) {
  if (!isMockAuth) {
    return NextResponse.json({ error: "Mock auth is disabled." }, { status: 400 });
  }

  const form = await req.formData().catch(() => null);
  // Dashboard is the default landing on sign-in (PRD §36).
  const redirectTo = (form?.get("from") as string | null) ?? "/dashboard";

  // The platform-role picker (one of the twelve — lib/roles.ts) takes
  // precedence; the coarse admin/member/viewer picker is kept for callers
  // (e.g. existing e2e specs) that still submit `role`.
  const platformRole = form?.get("platformRole") as string | null;
  if (platformRole) {
    if (!isPlatformRole(platformRole)) {
      return NextResponse.json({ error: "Invalid platform role." }, { status: 400 });
    }
    const session = buildMockSessionForPlatformRole(platformRole);
    const res = NextResponse.redirect(new URL(redirectTo, req.url), { status: 303 });
    res.cookies.set(MOCK_COOKIE_NAME, encodeSession(session), MOCK_COOKIE_OPTIONS);
    return res;
  }

  const role = (form?.get("role") as Role | null) ?? "admin";
  if (!ALLOWED.includes(role)) {
    return NextResponse.json({ error: "Invalid role." }, { status: 400 });
  }

  const session = buildMockSession(role);
  const res = NextResponse.redirect(new URL(redirectTo, req.url), { status: 303 });
  res.cookies.set(MOCK_COOKIE_NAME, encodeSession(session), MOCK_COOKIE_OPTIONS);
  return res;
}
