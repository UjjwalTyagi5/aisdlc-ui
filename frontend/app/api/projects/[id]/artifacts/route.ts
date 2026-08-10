import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const search = req.nextUrl.searchParams.toString();
  const path = search
    ? `/projects/${encodeURIComponent(id)}/artifacts?${search}`
    : `/projects/${encodeURIComponent(id)}/artifacts`;
  const data = await bffFetch(path, { session });
  return Response.json(data);
}
