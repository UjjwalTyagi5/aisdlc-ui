import { type NextRequest } from "next/server";
import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";
export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string; session: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id, session: sid } = await params;
  void id;
  return Response.json(await bffFetch(`/sdlc/agent/documentation/docset/${encodeURIComponent(sid)}`, { session }));
}
