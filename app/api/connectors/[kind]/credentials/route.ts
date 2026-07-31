import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * POST /api/connectors/[kind]/credentials
 *
 * Forwards pasted connector credentials (Azure DevOps PAT, Jira email + API
 * token) to FastAPI, which stores them in the tenant secret store and runs a
 * live verify probe. The credential travels server-to-server only; the browser
 * receives just the verification result.
 */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ kind: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { kind } = await params;
  const body = await req.json().catch(() => ({}));
  const data = await bffFetch(`/connectors/${encodeURIComponent(kind)}/credentials`, {
    session,
    method: "POST",
    body,
  });
  return Response.json(data);
}
