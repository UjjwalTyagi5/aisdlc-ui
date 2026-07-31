import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * POST /api/runs/[id]/copilot/cancel-turn — stop the Copilot's in-flight turn.
 * Signed BFF proxy to FastAPI `POST /runs/{id}/copilot/cancel-turn` (mirrors the
 * `set-stage` proxy). Only signals the live WS turn to stop streaming; the run
 * stays open (unlike the whole-run `/cancel`). Backs the composer Stop button.
 */
export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const data = await bffFetch(
    `/runs/${encodeURIComponent(id)}/copilot/cancel-turn`,
    { session, method: "POST", body: {} },
  );
  return Response.json(data);
}
