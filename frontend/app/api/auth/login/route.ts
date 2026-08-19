import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import {
  MOCK_COOKIE_MAX_AGE,
  MOCK_COOKIE_NAME,
  encodeSession,
} from "@/lib/auth/mock";
import { buildLocalSession, type LoginResponse } from "@/lib/auth/local";
import { isLocalAuth } from "@/lib/auth/mode";
import {
  TOKEN_COOKIE_NAME,
  tokenCookieOptions,
  verifyBackendToken,
} from "@/lib/auth/token";

/**
 * Server-only login proxy (Phase 3 local email+password auth).
 *
 * The browser POSTs {email, password} here; this handler forwards to the
 * FastAPI `POST /auth/login` over the server-only internal URL (the password
 * never transits a public/browser-visible base URL), then on success stores the
 * backend's own signed token in an httpOnly cookie.
 *
 * THE TOKEN IS STORED, NOT RE-MINTED. This handler used to discard
 * `LoginResponse.token` and write an unsigned base64 session blob instead, which
 * `lib/bff/jwt.ts` then re-signed into the token FastAPI trusts — so anyone who
 * edited that cookie minted themselves `admin:*`. The `sdlc_session` cookie
 * survives for display fields only (name, email, organization label) and is now
 * httpOnly; `getSession()` takes identity, tenant and permissions from the
 * verified token and ignores whatever the display cookie claims about them.
 * See finding 1 in `docs/rbac-audit-2026-08-17.md`.
 */
const FASTAPI_BASE =
  process.env["FASTAPI_INTERNAL_URL"] ?? "http://localhost:8001";

export async function POST(req: Request) {
  if (!isLocalAuth) {
    return NextResponse.json(
      { error: "Email/password sign-in is disabled." },
      { status: 400 },
    );
  }

  const body = (await req.json().catch(() => null)) as {
    email?: string;
    password?: string;
  } | null;
  const email = body?.email?.trim();
  const password = body?.password;
  if (!email || !password) {
    return NextResponse.json(
      { error: "Enter your email and password." },
      { status: 400 },
    );
  }

  let res: Response;
  try {
    res = await fetch(`${FASTAPI_BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      { error: "Couldn't reach the sign-in service. Is the backend running?" },
      { status: 502 },
    );
  }

  if (res.status === 401) {
    return NextResponse.json(
      { error: "That email and password don't match." },
      { status: 401 },
    );
  }
  if (!res.ok) {
    return NextResponse.json(
      { error: "Sign-in failed. Please try again." },
      { status: 502 },
    );
  }

  const data = (await res.json()) as LoginResponse;

  // Verify the token we were just handed before storing it. A backend that
  // returned something unsignable — a misconfigured JWT_SECRET_KEY on either
  // side is the realistic case — must fail the sign-in loudly here rather than
  // produce a session that 401s on every subsequent call.
  const claims = await verifyBackendToken(data.token);
  if (!claims) {
    return NextResponse.json(
      { error: "Sign-in succeeded but the session could not be established." },
      { status: 502 },
    );
  }

  const session = buildLocalSession(data, email);

  const store = await cookies();
  store.set(TOKEN_COOKIE_NAME, data.token, tokenCookieOptions(claims));
  store.set(MOCK_COOKIE_NAME, encodeSession(session), {
    // httpOnly: the display cookie is no longer authoritative for anything, but
    // it still carries an email address and there is no reason for page script
    // to read it. Server components reach it through getSession().
    httpOnly: true,
    path: "/",
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    maxAge: MOCK_COOKIE_MAX_AGE,
  });

  return NextResponse.json({
    tier: data.tier,
    // Everyone lands on the Dashboard (PRD §36) — there is no second console to
    // route to. A user with no bindings yet sees an empty one; that is what
    // /my-access explains, and the sign-up flow sends them straight there.
    redirectTo: data.permissions.length === 0 ? "/my-access" : "/dashboard",
  });
}
