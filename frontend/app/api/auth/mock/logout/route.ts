import { NextResponse } from "next/server";

import { MOCK_COOKIE_NAME } from "@/lib/auth/mock";

/** Mock-mode logout — clears the session cookie and returns to the landing (/). */
export function GET(req: Request) {
  const res = NextResponse.redirect(new URL("/", req.url));
  res.cookies.delete(MOCK_COOKIE_NAME);
  return res;
}
