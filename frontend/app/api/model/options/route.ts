import { bffProxy } from "@/lib/bff/proxy";

/**
 * Selectable models and the active default — proxied to FastAPI
 * `GET /model/options`, the LiteLLM-backed catalogue the seam comment here was
 * waiting for.
 *
 * The backend resolves the default against the active workspace, which travels
 * as the `X-Workspace-Id` header `bffFetch` adds from the
 * `sdlc_active_workspace` cookie — so the per-unit default arrives without this
 * handler passing anything.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return bffProxy("/model/options");
}
