import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/** GET /api/dev/[id]/ado/repos/[project]/[repo]/branches — list branches for a repo. */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; project: string; repo: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id, project, repo } = await params;
  const provider = req.nextUrl.searchParams.get("provider");
  const qs = provider ? `?provider=${encodeURIComponent(provider)}` : "";
  const data = await bffFetch(
    `/dev/${encodeURIComponent(id)}/ado/repos/${encodeURIComponent(project)}/${encodeURIComponent(repo)}/branches${qs}`,
    { session },
  );
  return Response.json(data);
}
