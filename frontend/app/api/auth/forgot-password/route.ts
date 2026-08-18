import { NextResponse } from "next/server";

import { isLocalAuth } from "@/lib/auth/mode";

/**
 * Server-only proxy for requesting a password-reset link.
 *
 * ALWAYS RETURNS THE SAME THING. The backend answers identically whether or not the
 * address has an account — otherwise this is an account enumerator, and a sharper one
 * than login because it needs no password to probe with. This handler must not add a
 * distinction the backend deliberately withheld, so even a transport failure reports
 * success to the browser and is logged server-side instead.
 *
 * The trade is deliberate: somebody whose email genuinely failed to send sees "check your
 * inbox" and retries. That is a worse experience for one user than leaking which of your
 * colleagues have accounts is for everyone.
 */
const FASTAPI_BASE =
  process.env["FASTAPI_INTERNAL_URL"] ?? "http://localhost:8001";

export async function POST(req: Request) {
  if (!isLocalAuth) {
    return NextResponse.json(
      { error: "Password reset is not available in this mode." },
      { status: 400 },
    );
  }

  const body = (await req.json().catch(() => null)) as { email?: string } | null;
  const email = body?.email?.trim();
  if (!email) {
    // The one legitimate distinction: an empty form field is the caller's own input,
    // and says nothing about which accounts exist.
    return NextResponse.json({ error: "Enter your email address." }, { status: 400 });
  }

  try {
    await fetch(`${FASTAPI_BASE}/auth/forgot-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
      cache: "no-store",
    });
  } catch {
    console.error("[forgot-password] backend unreachable — no email will arrive");
  }

  return NextResponse.json({ ok: true });
}
