import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * Development workspace change set. Proxies FastAPI
 * `GET /runs/{id}/workspace/changes` → `{ base, files: [{ path, status,
 * additions, deletions }] }` so the Copilot Artifacts code-tree can decorate
 * changed files the same way the standalone Development page does.
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
  const data = await bffFetch(
    `/runs/${encodeURIComponent(id)}/workspace/changes`,
    { session },
  );
  return Response.json(data);
}
