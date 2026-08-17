import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { MOCK_COOKIE_NAME } from "@/lib/auth/mock";
import { isLocalAuth } from "@/lib/auth/mode";
import { TOKEN_COOKIE_NAME } from "@/lib/auth/token";

/**
 * Local-auth logout — revokes the token, clears both cookies, and returns to the
 * marketing landing (/), which hosts the sign-in modal. Supports GET (the org
 * user-menu uses an <a href>) and POST (forms).
 *
 * REVOKING IS THE POINT, not clearing. Deleting a cookie ends the session in
 * this browser and nowhere else; a token copied off the wire stayed valid for
 * its full lifetime afterwards. That was survivable while the BFF minted a fresh
 * token per request, but the browser now holds one backend-issued token for the
 * whole session, so logout has to tell the backend. See
 * docs/rbac-audit-2026-08-17.md.
 */
const FASTAPI_BASE =
  process.env["FASTAPI_INTERNAL_URL"] ?? "http://localhost:8001";

async function revokeToken(): Promise<void> {
  if (!isLocalAuth) return;
  const token = (await cookies()).get(TOKEN_COOKIE_NAME)?.value;
  if (!token) return;
  try {
    await fetch(`${FASTAPI_BASE}/auth/logout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
  } catch {
    // Best-effort. A backend that cannot be reached must not strand the user in
    // a signed-in shell — the cookies still go, and the token still expires on
    // its own schedule.
  }
}

async function clearAndRedirect(req: Request) {
  await revokeToken();
  const res = NextResponse.redirect(new URL("/", req.url), { status: 303 });
  res.cookies.delete(MOCK_COOKIE_NAME);
  res.cookies.delete(TOKEN_COOKIE_NAME);
  return res;
}

export async function GET(req: Request) {
  return clearAndRedirect(req);
}

export async function POST(req: Request) {
  return clearAndRedirect(req);
}
