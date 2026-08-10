import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ kind: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { kind } = await params;
  const data = await bffFetch(`/connectors/${encodeURIComponent(kind)}/disconnect`, {
    session,
    method: "POST",
  });
  return Response.json(data);
}
