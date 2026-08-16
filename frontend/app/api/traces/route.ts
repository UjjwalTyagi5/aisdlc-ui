import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Trace list — proxied to FastAPI `GET /traces`, the Langfuse-backed read the
 * seam comment here was waiting for.
 *
 * The scope filter moves to the backend with it. The reason it exists does not
 * change: a trace carries prompts, tool calls and model output from a specific
 * project — the richest cross-project payload on the platform, and the one place
 * where "read-only visibility" would still expose another team's requirements
 * verbatim. The backend narrows it against real bindings instead of the fixture
 * membership store.
 *
 * Filters travel as-is rather than being re-applied here; the backend spells them
 * the same way, and a second pass in this tier could only drop rows the backend
 * meant to return.
 */
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const search = req.nextUrl.searchParams.toString();
  return bffProxy(`/traces${search ? `?${search}` : ""}`);
}
