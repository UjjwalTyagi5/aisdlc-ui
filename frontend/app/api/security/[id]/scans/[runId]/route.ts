import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string; runId: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id, runId } = await params;
  const data = await bffFetch(
    `/security/${encodeURIComponent(id)}/scans/${encodeURIComponent(runId)}`,
    { session },
  );
  return Response.json(data);
}
