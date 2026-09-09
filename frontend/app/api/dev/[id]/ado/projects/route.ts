import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/** GET /api/dev/[id]/ado/projects — list ADO projects for the connector. */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  // `provider` is forwarded only when the dialog offered a choice; absent, the backend
  // uses the project's single configured source exactly as before.
  const provider = req.nextUrl.searchParams.get("provider");
  const qs = provider ? `?provider=${encodeURIComponent(provider)}` : "";
  const data = await bffFetch(`/dev/${encodeURIComponent(id)}/ado/projects${qs}`, { session });
  return Response.json(data);
}
