import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * GET /api/connectors/[kind]/oauth/callback
 *
 * BFF callback route — Wave C (REQ-M7-22).
 *
 * The OAuth provider redirects the user's browser to
 * /integrations/callback?code=...&state=...&kind=... which the
 * OAuthCallbackHandler client page picks up and calls this BFF endpoint.
 *
 * This handler:
 * 1. Guards with getSession() — returns 401 when unauthenticated.
 * 2. Reads `code` + `state` from query params (forwarded from the OAuth provider).
 * 3. Forwards the GET to FastAPI /connectors/{kind}/oauth/callback?code=&state=
 *    where the HMAC CSRF state is verified server-side (T-7.4-23).
 * 4. Returns the resulting Connector record (installed: true) to the client.
 *
 * The raw OAuth tokens are NEVER sent to the browser — FastAPI exchanges
 * the code and writes the tokens directly to the tenant KV namespace
 * (T-7.4-24, UI-SPEC BFF Route Shape Contract).
 */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ kind: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { kind } = await params;
  const code = req.nextUrl.searchParams.get("code") ?? "";
  const state = req.nextUrl.searchParams.get("state") ?? "";

  const data = await bffFetch(
    `/connectors/${encodeURIComponent(kind)}/oauth/callback?code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}`,
    { session, method: "GET" },
  );
  return Response.json(data);
}
