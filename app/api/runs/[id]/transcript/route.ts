import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * Saved-run transcript read (P1b). Proxies FastAPI `GET /runs/{id}/transcript`
 * → `{ messages: [{ role: "user"|"agent", content, stage? }] }` so a reopened
 * run can replay its conversation before the WS reconnects.
 *
 * Server-side JWT boundary is identical to the sibling `/runs/[id]/*` routes —
 * the BFF bearer never reaches the browser.
 */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const data = await bffFetch(`/runs/${encodeURIComponent(id)}/transcript`, { session });
  return Response.json(data);
}
