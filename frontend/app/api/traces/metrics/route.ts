import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Trace metrics strip — proxied to FastAPI `GET /traces/metrics`.
 *
 * Computed by the backend over the same set `GET /traces` returns, which is what
 * keeps the guarantee the seam comment made here: the metrics strip can never
 * claim more traces than the list beneath it shows.
 */
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const search = req.nextUrl.searchParams.toString();
  return bffProxy(`/traces/metrics${search ? `?${search}` : ""}`);
}
