import { NextResponse } from "next/server";

import { MOCK_COOKIE_NAME } from "@/lib/auth/mock";

/**
 * Local-auth logout — clears the session cookie and returns to the marketing
 * landing (/), which hosts the sign-in modal. Supports GET (the org user-menu
 * uses an <a href>) and POST (the platform console uses a <form method="POST">).
 * Local + mock share the cookie name.
 */
function clearAndRedirect(req: Request) {
  const res = NextResponse.redirect(new URL("/", req.url), { status: 303 });
  res.cookies.delete(MOCK_COOKIE_NAME);
  return res;
}

export function GET(req: Request) {
  return clearAndRedirect(req);
}

export function POST(req: Request) {
  return clearAndRedirect(req);
}
