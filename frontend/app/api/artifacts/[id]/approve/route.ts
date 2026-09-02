import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * Accept an artifact into the project's shared record.
 *
 * Forwards rather than authorises: the backend gates this on the `approve` permission
 * AND on the caller administering the project the artifact belongs to. The session
 * check here only establishes that there IS a caller to forward as.
 */
export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const data = await bffFetch(`/artifacts/${encodeURIComponent(id)}/approve`, {
    session,
    method: "POST",
  });
  return Response.json(data);
}
