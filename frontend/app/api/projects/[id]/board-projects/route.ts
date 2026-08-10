import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/** GET /api/projects/[id]/board-projects — discover the connected board's projects. */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  // Forward the query string (e.g. ?provider=jira) so the backend reads the chosen board.
  const search = req.nextUrl.searchParams.toString();
  const path = `/projects/${encodeURIComponent(id)}/board-projects${search ? `?${search}` : ""}`;
  const data = await bffFetch(path, { session });
  return Response.json(data);
}
