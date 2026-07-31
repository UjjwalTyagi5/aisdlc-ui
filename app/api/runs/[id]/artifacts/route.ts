import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * Saved-run artifacts read (P1). Proxies FastAPI `GET /runs/{id}/artifacts`
 * → `{ artifacts: Artifact[] }` so the Artifacts panel can populate from
 * persisted data on open/reload without waiting on the WS.
 *
 * Server-side JWT boundary is identical to the sibling `/runs/[id]/*` routes.
 */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const data = await bffFetch(`/runs/${encodeURIComponent(id)}/artifacts`, { session });
  return Response.json(data);
}
