import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";

/**
 * Forward one call to FastAPI and return ITS answer — status and body — to the browser.
 *
 * WHY. A route that simply `await bffFetch(...)` turns every backend refusal into a bare
 * 500: `bffFetch` throws on a non-2xx, nothing catches it, and Next.js answers "Internal
 * Server Error". So "Test cases are already being generated for this project" (409) or "No
 * source-code connector is configured" arrived as "Request failed", with the reason gone.
 */
export async function forward(path: string, init: { method?: "GET" | "POST"; body?: unknown; status?: number } = {}) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated", detail: "Sign in again." }, { status: 401 });
  try {
    const data = await bffFetch(path, { session, method: init.method ?? "GET", body: init.body });
    return Response.json(data ?? {}, { status: init.status ?? 200 });
  } catch (err) {
    if (err instanceof ApiRequestError) {
      return Response.json(err.rawBody ?? { detail: err.message }, { status: err.status });
    }
    return Response.json({ detail: "The backend could not be reached." }, { status: 502 });
  }
}
