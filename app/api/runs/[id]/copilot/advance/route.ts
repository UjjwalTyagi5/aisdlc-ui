import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * POST /api/runs/[id]/copilot/advance — advance (or re-run) a CONVERSATIONAL run
 * at a gate. Signed BFF proxy to FastAPI `POST /runs/{id}/copilot/advance`; the
 * server re-checks the stage approve permission (no self-approval bypass). Used by
 * the Copilot gate instead of the Temporal `hitl.decision` signal (there is no
 * workflow for a conversational run).
 */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const body: unknown = await req.json();
  const data = await bffFetch(
    `/runs/${encodeURIComponent(id)}/copilot/advance`,
    { session, method: "POST", body },
  );
  return Response.json(data);
}
