import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/** POST /api/projects/[id]/ingest-board — pull board work items into stories. */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const body = await req.json().catch(() => ({}));
  const data = await bffFetch(`/projects/${encodeURIComponent(id)}/ingest-board`, {
    session,
    method: "POST",
    body,
  });
  return Response.json(data);
}
