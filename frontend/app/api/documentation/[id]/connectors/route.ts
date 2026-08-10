import { type NextRequest } from "next/server";
import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";
export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id } = await params;
  return Response.json(await bffFetch(`/documentation/${encodeURIComponent(id)}/connectors`, { session }));
}
