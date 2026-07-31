import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { createDraft } from "@/lib/mock/agent-profile-fixtures";
import type { AgentProfileDraftInput } from "@/lib/schemas/agent-profiles";

const FIELD_CAPS = { prompt_prepend: 4000, prompt_append: 4000, output_contract_extra: 2000 } as const;

// DUMMY-DATA SEAM: mirrors mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
// 422 lint-violation shape ({detail: {violations}}) matches what
// getLintViolations() in lib/api/agent-profiles.ts already expects — kept
// from the previous bffFetch-based implementation for zero client churn.
export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const body = (await req.json()) as AgentProfileDraftInput;
  if (!body?.agent_id || !body?.scope) {
    return Response.json({ code: "invalid_input", message: "agent_id and scope are required" }, { status: 422 });
  }

  const violations = (
    [
      ["prompt_prepend", body.prompt_prepend ?? ""],
      ["prompt_append", body.prompt_append ?? ""],
      ["output_contract_extra", body.output_contract_extra ?? ""],
    ] as const
  )
    .filter(([field, value]) => value.length > FIELD_CAPS[field])
    .map(([field]) => ({
      field,
      code: "too_long",
      message: `Over the ${FIELD_CAPS[field].toLocaleString()}-character limit.`,
    }));
  if (violations.length > 0) {
    return Response.json({ detail: { violations } }, { status: 422 });
  }

  const created = createDraft({
    agentId: body.agent_id,
    scope: body.scope,
    scopeId: body.scope_id ?? null,
    promptPrepend: body.prompt_prepend ?? "",
    promptAppend: body.prompt_append ?? "",
    outputContractExtra: body.output_contract_extra ?? "",
    createdBy: session.user.name,
  });
  return Response.json(created);
}
