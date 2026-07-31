import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * Per-stage generated-files tree read. Proxies FastAPI
 * `GET /runs/{id}/stage-files/{stage}/tree` → `{ ready, paths, truncated }` so
 * the Copilot Artifacts panel can browse any downstream agent's output dir
 * (design, testing, …) through the same `CodeTreeView` used for Development's
 * pulled workspace.
 *
 * Server-side JWT boundary is identical to the sibling `/runs/[id]/*` routes —
 * the BFF bearer never reaches the browser.
 */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string; stage: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id, stage } = await params;
  const data = await bffFetch(
    `/runs/${encodeURIComponent(id)}/stage-files/${encodeURIComponent(stage)}/tree`,
    { session },
  );
  return Response.json(data);
}
