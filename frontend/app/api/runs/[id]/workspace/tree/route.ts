import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * Development workspace tree read. Proxies FastAPI `GET /runs/{id}/workspace/tree`
 * → `{ ready, paths, truncated, repo_name?, branch? }` so the Copilot Artifacts
 * panel can render the pulled repo's code tree while the Development stage runs.
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
  const data = await bffFetch(`/runs/${encodeURIComponent(id)}/workspace/tree`, { session });
  return Response.json(data);
}
