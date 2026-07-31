import { type NextRequest } from "next/server";
import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id } = await params;
  const body: unknown = await req.json();
  return Response.json(await bffFetch(`/documentation/${encodeURIComponent(id)}/prepare`, { session, method: "POST", body }));
}
