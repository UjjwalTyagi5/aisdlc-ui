import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/** GET /api/dev/[id]/workspace/file/changed-lines?path=... — new-file changed line numbers. */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const path = req.nextUrl.searchParams.get("path");
  if (!path) return Response.json({ code: "missing_path" }, { status: 400 });

  const data = await bffFetch(
    `/dev/${encodeURIComponent(id)}/workspace/file/changed-lines?path=${encodeURIComponent(path)}`,
    { session },
  );
  return Response.json(data);
}
