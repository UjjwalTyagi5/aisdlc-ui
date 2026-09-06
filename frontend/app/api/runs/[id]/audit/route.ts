import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

type P = { params: Promise<{ id: string }> };

/**
 * A run's audit trail. `withQuery` because the client filters on cursor, agent,
 * event_type, actor, since and until — dropping the query string would silently
 * return the unfiltered first page and look like the filters did nothing.
 */
export async function GET(req: NextRequest, { params }: P) {
  const { id } = await params;
  return forward(req, `/runs/${encodeURIComponent(id)}/audit`, { withQuery: true });
}
