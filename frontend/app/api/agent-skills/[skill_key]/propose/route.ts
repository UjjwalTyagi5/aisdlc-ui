import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { GovernanceApproval } from "@/lib/schemas/governance-approval";

/**
 * Propose a change to a custom skill at a tier you don't own — proxied to
 * FastAPI `POST /agent-skills/{skill_key}/propose`.
 *
 * Unlike `/agent-profiles/{id}/propose` (which takes no body — the backend
 * resolves everything from the loaded profile row), Skills has no single-row-id
 * path param anywhere else in this API, so the request DOES forward a body —
 * but only `{agent_id, scope, scope_id}` (see `SkillProposeInput`), never a
 * `target_ref`/`version`: the backend resolves its own target server-side via
 * `get_latest_draft_version`, so the client still cannot name which row gets
 * published. This route was missing entirely before the sub-project 3 final
 * whole-branch review (Critical #2) — the client call fell through to the
 * GET-only `[skill_key]/[detail_key]/route.ts` and 405'd.
 */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ skill_key: string }> },
) {
  const { skill_key: skillKey } = await params;
  const body: unknown = await req.json();
  return bffProxy(`/agent-skills/${encodeURIComponent(skillKey)}/propose`, {
    method: "POST",
    body,
    schema: GovernanceApproval,
  });
}
