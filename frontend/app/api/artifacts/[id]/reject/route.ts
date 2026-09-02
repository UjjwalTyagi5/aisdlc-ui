import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/** Decline an artifact. Its pending bytes are deleted; the row stays as the record. */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const body: unknown = await req.json().catch(() => ({}));
  const data = await bffFetch(`/artifacts/${encodeURIComponent(id)}/reject`, {
    session,
    method: "POST",
    body,
  });
  return Response.json(data);
}
