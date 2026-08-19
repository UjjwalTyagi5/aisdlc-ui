import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { MOCK_COOKIE_NAME, MOCK_COOKIE_MAX_AGE, encodeSession } from "@/lib/auth/mock";
import { buildLocalSession, type LoginResponse } from "@/lib/auth/local";
import { isLocalAuth } from "@/lib/auth/mode";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";
import {
  TOKEN_COOKIE_NAME,
  tokenCookieOptions,
  verifyBackendToken,
} from "@/lib/auth/token";

/**
 * Swap this session's token for one minted against what the user holds NOW.
 *
 * Permissions and `platform_role` are baked in at login, so a role granted afterwards
 * does not reach a session already holding one. The symptom is worse than a delay:
 * project lists are resolved live from bindings, so the dashboard shows the project
 * somebody was just given while the navigation and the role chip still read the stale
 * claim and offer nothing to do with it.
 *
 * THE BACKEND MINTS, THIS ONLY STORES. `/auth/refresh` is authenticated by the token
 * it replaces, and the new one is verified here before it is written — a backend that
 * returned something unsignable must fail loudly rather than leave a cookie that 401s
 * every subsequent call. The BFF has never been an issuer and is not becoming one.
 *
 * WIDENING ONLY. A stale token is UNDER-privileged, so this can never hand anybody
 * more than the database says they have. Reductions are the token epoch's job — it
 * refuses the stale token outright rather than waiting to be asked.
 *
 * Returns 204 for every "nothing to do" case, so the caller can fire it blindly
 * without branching on auth mode or on being signed in.
 */
export const dynamic = "force-dynamic";

export async function POST() {
  if (!isLocalAuth) return new NextResponse(null, { status: 204 });

  const session = await getSession();
  if (!session) return new NextResponse(null, { status: 204 });

  let data: LoginResponse;
  try {
    data = (await bffFetch("/auth/refresh", {
      session,
      method: "POST",
    })) as LoginResponse;
  } catch {
    // A failed refresh must never sign anybody out. They keep the token they have,
    // which still works, and simply do not see the new role until next time.
    return new NextResponse(null, { status: 204 });
  }

  const claims = await verifyBackendToken(data.token);
  if (!claims) return new NextResponse(null, { status: 204 });

  const store = await cookies();
  store.set(TOKEN_COOKIE_NAME, data.token, tokenCookieOptions(claims));
  store.set(
    MOCK_COOKIE_NAME,
    encodeSession(buildLocalSession(data, session.user.email)),
    {
      httpOnly: true,
      path: "/",
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      maxAge: MOCK_COOKIE_MAX_AGE,
    },
  );

  return NextResponse.json({
    platformRole: data.platform_role ?? null,
    permissions: data.permissions,
  });
}
