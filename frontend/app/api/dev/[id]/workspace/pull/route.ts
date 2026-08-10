import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/** POST /api/dev/[id]/workspace/pull — clone/pull a repo into the dev workspace. */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const body = await req.json().catch(() => ({}));
  const data = await bffFetch(`/dev/${encodeURIComponent(id)}/workspace/pull`, {
    session,
    method: "POST",
    body,
  });
  return Response.json(data);
}
