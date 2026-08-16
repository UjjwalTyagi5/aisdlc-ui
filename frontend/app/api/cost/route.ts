import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Spend breakdown — proxied to FastAPI `GET /cost`.
 *
 * Previously a DUMMY-DATA SEAM that derived a breakdown from the workspace and
 * project fixtures, so the Cost page reported spend for units and projects the
 * database has never held.
 *
 * The scope filter that ran here is the backend's now, and belongs there: it was
 * intersecting the viewer's readable units against the fixture stores, while the
 * backend narrows against real bindings. The note it carried is still true and
 * still the reason the endpoint is scoped at all — an unfiltered TOTAL leaks even
 * without a per-unit breakdown, because "the organisation spent $21,889" tells a
 * Business Unit Admin what the other units cost.
 */
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const search = req.nextUrl.searchParams.toString();
  return bffProxy(`/cost${search ? `?${search}` : ""}`);
}
