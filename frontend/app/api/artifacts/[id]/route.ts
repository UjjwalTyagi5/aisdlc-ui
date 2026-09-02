import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const data = await bffFetch(`/artifacts/${encodeURIComponent(id)}`, { session });
  return Response.json(data);
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const body: unknown = await req.json();
  const data = await bffFetch(`/artifacts/${encodeURIComponent(id)}`, {
    session,
    method: "PATCH",
    body,
  });
  return Response.json(data);
}

/** Permanently delete an artifact and its stored file.
 *
 * Returns 204 with no body, mirroring the resource API. The backend gates this on the
 * `artifact:delete` permission and re-checks tenant and project visibility, so this
 * handler forwards rather than authorising — the session check here only establishes
 * that there IS a caller to forward as.
 */
export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  await bffFetch(`/artifacts/${encodeURIComponent(id)}`, {
    session,
    method: "DELETE",
  });
  return new Response(null, { status: 204 });
}
