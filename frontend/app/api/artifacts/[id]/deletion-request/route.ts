import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * Ask the document's owner to delete it. NOTHING IS DELETED HERE.
 *
 * Forwards rather than authorises: the backend gates this on `artifact:delete`, checks
 * the caller can see the project, and decides the approver from the document's own
 * stage. The session check here only establishes that there IS a caller to forward as.
 *
 * The BODY IS FORWARDED because the reason lives in it and the backend 422s without
 * one — an approver asked to destroy something irreversibly needs to know why.
 */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const body: unknown = await req.json();
  const data = await bffFetch(
    `/artifacts/${encodeURIComponent(id)}/deletion-request`,
    { session, method: "POST", body },
  );
  return Response.json(data);
}
