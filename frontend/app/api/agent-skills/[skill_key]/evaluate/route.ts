import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { EvaluationResult } from "@/lib/schemas/agent-studio-eval";

/** Proxied to FastAPI POST /agent-skills/{skill_key}/evaluate. Forwards the body
 *  (agent_id/scope/scope_id) — same shape as the sibling propose route, and for
 *  the same reason: Skills has no single-row-id path param to key off of. */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ skill_key: string }> },
) {
  const { skill_key: skillKey } = await params;
  const body: unknown = await req.json();
  return bffProxy(`/agent-skills/${encodeURIComponent(skillKey)}/evaluate`, {
    method: "POST",
    body,
    schema: EvaluationResult,
  });
}
