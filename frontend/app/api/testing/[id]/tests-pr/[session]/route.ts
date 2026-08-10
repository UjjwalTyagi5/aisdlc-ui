import { type NextRequest } from "next/server";
import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";
export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string; session: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { session: sid } = await params;
  return Response.json(await bffFetch(`/sdlc/agent/testing/tests-pr/${encodeURIComponent(sid)}`, { session, method: "POST", body: {} }));
}
