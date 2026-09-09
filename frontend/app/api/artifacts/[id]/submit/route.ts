import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * Put a draft document forward for approval.
 *
 * Forwards rather than authorises: the backend gates this on `run:create` and on the
 * artifact being visible to the caller's project. The session check here only
 * establishes that there IS a caller to forward as.
 *
 * `run:create`, not `approve` — asking for a decision is producing work, not accepting
 * it, so the person who ran the agent may raise their own output.
 */
export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const data = await bffFetch(`/artifacts/${encodeURIComponent(id)}/submit`, {
    session,
    method: "POST",
  });
  return Response.json(data);
}
