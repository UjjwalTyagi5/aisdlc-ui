import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { EvaluationResult } from "@/lib/schemas/agent-studio-eval";

/** Proxied to FastAPI POST /agent-profiles/{id}/evaluate. No request body — same
 *  convention as /publish, /unpublish, /propose (all keyed by the draft id alone). */
export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/agent-profiles/${encodeURIComponent(id)}/evaluate`, {
    method: "POST",
    schema: EvaluationResult,
  });
}
