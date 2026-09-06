import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";

/**
 * Proxy one BFF route to the same path on the backend.
 *
 * WHY THIS EXISTS. Every browser call goes to `/api/...` and is forwarded by a Next
 * route handler; the backend is not reachable directly. A backend route with no
 * handler here is invisible to the product — the browser gets Next's own 404 ("Not
 * Found"), which looks like a backend problem and is not one. That is exactly how the
 * artifact-version panel shipped broken while every backend test passed: the tests
 * called FastAPI through TestClient and never crossed this boundary.
 *
 * NOT A CATCH-ALL, deliberately. Nothing else in `app/api` is one. An enumerated
 * surface means a backend route becomes reachable only when somebody adds it here,
 * which is the same "one conscious decision per route" rule the backend enforces with
 * `assert_all_routes_protected`.
 *
 * The error shape matches the hand-written handlers: `ApiRequestError.details` when
 * the backend said something useful, so the UI can show the real refusal rather than
 * a generic failure.
 */
export async function forward(
  req: NextRequest,
  path: string,
  opts: {
    method?: "GET" | "POST";
    withBody?: boolean;
    /**
     * Append this request's query string to the forwarded path.
     *
     * OPT-IN, not automatic. A proxy that forwards whatever it is given lets a
     * caller reach filters the route never meant to expose, and the enumerated
     * surface this file exists for would stop meaning much. Routes whose backend
     * takes no query parameters simply do not set it.
     */
    withQuery?: boolean;
  } = {},
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const method = opts.method ?? "GET";
  if (opts.withQuery) {
    const search = req.nextUrl.searchParams.toString();
    if (search) path = `${path}?${search}`;
  }
  let body: unknown;
  if (opts.withBody) {
    try {
      body = await req.json();
    } catch {
      // An empty body is legitimate for the POSTs that take no payload.
      body = undefined;
    }
  }

  try {
    return Response.json(await bffFetch(path, { session, method, body }));
  } catch (err) {
    if (err instanceof ApiRequestError) {
      return Response.json(
        err.details ?? { code: err.code, message: err.message },
        { status: err.status },
      );
    }
    throw err;
  }
}
