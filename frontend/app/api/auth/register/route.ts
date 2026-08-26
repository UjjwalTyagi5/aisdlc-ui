import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import {
  MOCK_COOKIE_MAX_AGE,
  MOCK_COOKIE_NAME,
  encodeSession,
} from "@/lib/auth/mock";
import { buildLocalSession, type LoginResponse } from "@/lib/auth/local";
import { isLocalAuth } from "@/lib/auth/mode";

/**
 * Server-only registration proxy (self-serve signup).
 *
 * Mirrors the login proxy: the browser POSTs {email, password} here, this forwards
 * to FastAPI `POST /auth/register` over the server-only internal URL so the password
 * never transits a browser-visible base URL, then seeds the same `sdlc_session`
 * cookie on success.
 *
 * The account joins the one existing organization with NO role bindings, so it
 * carries an empty permission set. Signing up is requesting access, not getting it —
 * which is why the response lands them on /my-access rather than the dashboard.
 */
const FASTAPI_BASE =
  process.env["FASTAPI_INTERNAL_URL"] ?? "http://localhost:8001";

export async function POST(req: Request) {
  if (!isLocalAuth) {
    return NextResponse.json(
      { error: "Sign-up is disabled." },
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
      { error: "Enter your email and a password." },
      { status: 400 },
    );
  }
  // Mirrors the backend's own floor so the common case is caught without a round trip.
  if (password.length < 8) {
    return NextResponse.json(
      { error: "Password must be at least 8 characters." },
      { status: 400 },
    );
  }

  let res: Response;
  try {
    res = await fetch(`${FASTAPI_BASE}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      { error: "Couldn't reach the sign-up service. Is the backend running?" },
      { status: 502 },
    );
  }

  // Unlike sign-in, a duplicate email is reported plainly: a signup form that
  // cannot say "that email is taken" is unusable, and the address is one the
  // person just typed about themselves.
  if (res.status === 409) {
    return NextResponse.json(
      { error: "An account with that email already exists. Try signing in." },
      { status: 409 },
    );
  }
  if (res.status === 422) {
    return NextResponse.json(
      { error: "Check your details — that email or password wasn't accepted." },
      { status: 422 },
    );
  }
  // The backend booted without ORG_ADMIN_* set, so no organization exists to join.
  // That is an operator misconfiguration, not something the visitor can fix.
  if (res.status === 503) {
    return NextResponse.json(
      { error: "Sign-up isn't available yet. Contact your administrator." },
      { status: 503 },
    );
  }
  if (!res.ok) {
    return NextResponse.json(
      { error: "Sign-up failed. Please try again." },
      { status: 502 },
    );
  }

  const data = (await res.json()) as LoginResponse;
  const session = buildLocalSession(data, email);

  const store = await cookies();
  store.set(MOCK_COOKIE_NAME, encodeSession(session), {
    httpOnly: false, // read by server components; XSS-mitigated via CSP
    path: "/",
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    maxAge: MOCK_COOKIE_MAX_AGE,
  });

  // /my-access, not /dashboard: the account has no permissions yet, so every real
  // page would refuse it. /my-access is ungated and is the one screen that can
  // truthfully explain the state they are in.
  return NextResponse.json({ tier: data.tier, redirectTo: "/my-access" });
}
