import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/** GET /api/dev/[id]/ado/projects/[project]/repos — list repos in an ADO project. */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string; project: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id, project } = await params;
  const data = await bffFetch(
    `/dev/${encodeURIComponent(id)}/ado/projects/${encodeURIComponent(project)}/repos`,
    { session },
  );
  return Response.json(data);
}
