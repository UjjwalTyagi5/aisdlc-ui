import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/** POST /api/runs/[id]/cancel — terminate the run's Temporal workflow + mark cancelled. */
export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const data = await bffFetch(`/runs/${encodeURIComponent(id)}/cancel`, {
    session,
    method: "POST",
  });
  return Response.json(data);
}
