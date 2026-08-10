import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * POST /api/runs/[id]/copilot/set-stage — repoint a CONVERSATIONAL run's active
 * stage to any known pipeline stage. Signed BFF proxy to FastAPI
 * `POST /runs/{id}/copilot/set-stage` (mirrors the `advance` proxy); the server
 * re-checks the TARGET stage's approve permission (no self-approval bypass).
 *
 * Backs the Copilot left-rail "click any stage to work with that agent" jump
 * (`pipeline-rail.tsx` + `useCopilot().setStage`) — the WS re-reads
 * `run.current_stage` on the next chat turn and routes to that agent.
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
    `/runs/${encodeURIComponent(id)}/copilot/set-stage`,
    { session, method: "POST", body },
  );
  return Response.json(data);
}
