import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/** A document's page copy — the markdown the app renders as a report.
 *
 * SAME-ORIGIN ON PURPOSE. The page used to fetch this straight from the backend's
 * `/generated/` mount, which the app's Content Security Policy (`connect-src 'self'`)
 * refuses — every generated document failed to render with "Failed to fetch". Through
 * the BFF it is the app's own origin, and the backend applies the usual project
 * visibility.
 *
 * A 404 ("no page view") and a 410 (rejected) are answers the page shows, so they are
 * relayed with their status rather than surfacing as a 500. */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  try {
    return Response.json(await bffFetch(`/artifacts/${encodeURIComponent(id)}/page`, { session }));
  } catch (e) {
    if (e instanceof ApiRequestError) {
      return Response.json({ code: e.code, message: e.message }, { status: e.status });
    }
    throw e;
  }
}
