import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * Delete the document if the caller owns it; otherwise raise a request for the person
 * who does.
 *
 * Forwards rather than authorises: the backend decides which of the two happens, using
 * the same helper its approve/reject routes use. A Project Admin owns every agent on
 * their project and a stage's own role owns its agent, so those callers get an outright
 * delete (204); everyone else gets a governance request (202).
 *
 * THE OUTCOME IS RETURNED EXPLICITLY rather than left for the client to infer from an
 * empty body. `bffFetch` collapses a 204 to `undefined`, so "deleted" and "the backend
 * returned nothing" would look identical — and a UI that guessed wrong would say a
 * request was raised while the file was actually gone.
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

  // 204 from the delete path arrives as undefined; the request path returns the
  // governance request it raised.
  const deleted = data === undefined || data === null;
  return Response.json({ deleted, request: deleted ? null : data });
}
