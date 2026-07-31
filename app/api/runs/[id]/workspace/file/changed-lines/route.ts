import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * Development workspace per-file changed lines. Proxies FastAPI
 * `GET /runs/{id}/workspace/file/changed-lines?path=<rel>` → `{ added_lines }`
 * so the Copilot Artifacts code viewer can highlight added lines like the
 * standalone Development page. The `path` query param is forwarded verbatim.
 *
 * Server-side JWT boundary is identical to the sibling `/runs/[id]/*` routes —
 * the BFF bearer never reaches the browser.
 */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const path = req.nextUrl.searchParams.get("path") ?? "";
  const data = await bffFetch(
    `/runs/${encodeURIComponent(id)}/workspace/file/changed-lines?path=${encodeURIComponent(path)}`,
    { session },
  );
  return Response.json(data);
}
