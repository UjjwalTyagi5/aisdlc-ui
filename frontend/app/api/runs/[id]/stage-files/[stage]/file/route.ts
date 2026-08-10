import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * Per-stage generated-file read. Proxies FastAPI
 * `GET /runs/{id}/stage-files/{stage}/file?path=<rel>` → `{ path, content,
 * size, binary, truncated }` so the Copilot Artifacts panel can open a file
 * from a downstream agent's generated-output dir. The `path` query param is
 * forwarded verbatim.
 *
 * Server-side JWT boundary is identical to the sibling `/runs/[id]/*` routes —
 * the BFF bearer never reaches the browser.
 */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; stage: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id, stage } = await params;
  const path = req.nextUrl.searchParams.get("path") ?? "";
  const data = await bffFetch(
    `/runs/${encodeURIComponent(id)}/stage-files/${encodeURIComponent(stage)}/file?path=${encodeURIComponent(path)}`,
    { session },
  );
  return Response.json(data);
}
