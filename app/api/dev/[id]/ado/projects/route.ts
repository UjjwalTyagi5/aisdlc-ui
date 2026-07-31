import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/** GET /api/dev/[id]/ado/projects — list ADO projects for the connector. */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const data = await bffFetch(`/dev/${encodeURIComponent(id)}/ado/projects`, { session });
  return Response.json(data);
}
