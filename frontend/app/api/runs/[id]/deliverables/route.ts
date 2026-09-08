import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * Orchestrator deliverables read. Proxies FastAPI `GET /runs/{id}/deliverables`
 * → `{ deliverables: Deliverable[] }` so the panel repopulates on reload without
 * waiting on the WS.
 *
 * Deliberately distinct from the sibling `/artifacts` route. That one reads what the
 * STANDALONE agents wrote, which carries an approval concept; the Orchestrator's
 * agents share their names and capability and are a different thing, and their output
 * is never gated. Two routes, because they are two concepts — not one route with a
 * flag, which is how they would drift back together.
 *
 * Server-side JWT boundary is identical to the other `/runs/[id]/*` routes.
 */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const data = await bffFetch(`/runs/${encodeURIComponent(id)}/deliverables`, { session });
  return Response.json(data);
}
