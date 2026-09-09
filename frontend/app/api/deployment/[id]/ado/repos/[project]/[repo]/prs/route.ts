import { type NextRequest } from "next/server";
import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";
export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string; project: string; repo: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id, project, repo } = await params;
  // Forwarded only when the dialog offered a choice; absent, the backend uses the
  // project's single configured source and answers exactly as it did before.
  const provider = req.nextUrl.searchParams.get("provider");
  const qs = provider ? `?provider=${encodeURIComponent(provider)}` : "";
  return Response.json(await bffFetch(`/deployment/${encodeURIComponent(id)}/ado/repos/${encodeURIComponent(project)}/${encodeURIComponent(repo)}/prs${qs}`, { session }));
}
