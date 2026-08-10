import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; name: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id, name } = await params;
  const body: unknown = await req.json();
  const data = await bffFetch(
    `/runs/${encodeURIComponent(id)}/signals/${encodeURIComponent(name)}`,
    { session, method: "POST", body },
  );
  return Response.json(data);
}
