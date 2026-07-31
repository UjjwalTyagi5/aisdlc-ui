import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * GET /api/runs/worker-status — whether a Temporal worker is polling the queue.
 * Static segment, so it resolves before the dynamic [id] route.
 */
export async function GET(_req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const data = await bffFetch(`/runs/worker-status`, { session });
  return Response.json(data);
}
