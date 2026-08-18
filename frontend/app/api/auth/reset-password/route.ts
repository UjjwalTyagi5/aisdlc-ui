import { NextResponse } from "next/server";

import { isLocalAuth } from "@/lib/auth/mode";

/**
 * Server-only proxy for spending a set-password link.
 *
 * Serves BOTH the onboarding invite and a forgotten-password reset — they are the same
 * token type, and this is the endpoint that sets a first password as well as a
 * replacement one.
 *
 * GET validates a token without spending it, so the page can say "this link has expired"
 * before the visitor types a password twice. POST sets the password.
 *
 * The token travels in the request body on POST rather than the query string: a URL is
 * logged by proxies and kept in browser history, and although this token is single-use
 * and short-lived it is a credential for as long as it is alive.
 */
const FASTAPI_BASE =
  process.env["FASTAPI_INTERNAL_URL"] ?? "http://localhost:8001";

export async function GET(req: Request) {
  if (!isLocalAuth) {
    return NextResponse.json({ status: "unknown" }, { status: 400 });
  }
  const token = new URL(req.url).searchParams.get("token") ?? "";
  if (!token) return NextResponse.json({ status: "unknown" });

  try {
    const res = await fetch(
      `${FASTAPI_BASE}/auth/reset-password/validate?token=${encodeURIComponent(token)}`,
      { cache: "no-store" },
    );
    const data = (await res.json().catch(() => ({}))) as { status?: string };
    return NextResponse.json({ status: data.status ?? "unknown" });
  } catch {
    // Not "unknown": that would tell the visitor their link is bad when the truth is we
    // could not check. The page renders the form and lets the POST be the arbiter.
    return NextResponse.json({ status: "unchecked" });
  }
}

export async function POST(req: Request) {
  if (!isLocalAuth) {
    return NextResponse.json(
      { error: "Password reset is not available in this mode." },
      { status: 400 },
    );
  }

  const body = (await req.json().catch(() => null)) as {
    token?: string;
    password?: string;
  } | null;
  const token = body?.token;
  const password = body?.password;

  if (!token) {
    return NextResponse.json(
      { error: "This link is missing its token. Request a new one." },
      { status: 400 },
    );
  }
  // Mirrors the backend floor so the common case is caught without a round trip.
  if (!password || password.length < 8) {
    return NextResponse.json(
      { error: "Password must be at least 8 characters." },
      { status: 400 },
    );
  }

  let res: Response;
  try {
    res = await fetch(`${FASTAPI_BASE}/auth/reset-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, new_password: password }),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      { error: "Couldn't reach the server. Try again in a moment." },
      { status: 502 },
    );
  }

  if (res.status === 400) {
    return NextResponse.json(
      { error: "This link is no longer valid. Request a new one." },
      { status: 400 },
    );
  }
  if (!res.ok) {
    return NextResponse.json(
      { error: "Couldn't set your password. Try again." },
      { status: 502 },
    );
  }

  // No session is issued here on purpose. Setting a password is not signing in, and the
  // backend has just invalidated every token this account held — so the visitor goes to
  // the login form and uses what they just chose, which also confirms it works.
  return NextResponse.json({ ok: true });
}
