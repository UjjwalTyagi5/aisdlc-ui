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
 * Server-only login proxy (Phase 3 local email+password auth).
 *
 * The browser POSTs {email, password} here; this handler forwards to the
 * FastAPI `POST /auth/login` over the server-only internal URL (the password
 * never transits a public/browser-visible base URL), then on success seeds the
 * `sdlc_session` cookie from the resolved session. Subsequent BFF → FastAPI
 * calls re-mint a backend JWT from that session (lib/bff/jwt.ts), so the raw
 * backend token is not stored client-side.
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
  const session = buildLocalSession(data, email);

  const store = await cookies();
  store.set(MOCK_COOKIE_NAME, encodeSession(session), {
    httpOnly: false, // read by server components; XSS-mitigated via CSP
    path: "/",
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    maxAge: MOCK_COOKIE_MAX_AGE,
  });

  return NextResponse.json({
    tier: data.tier,
    // Org-tier users land on the Dashboard (PRD §36); the platform (Application
    // Team) tier keeps its own console.
    redirectTo: data.tier === "platform" ? "/platform" : "/dashboard",
  });
}
