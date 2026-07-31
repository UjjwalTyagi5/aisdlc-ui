import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { buildPreview } from "@/lib/mock/agent-profile-fixtures";
import type { AgentProfileDraftInput } from "@/lib/schemas/agent-profiles";

// DUMMY-DATA SEAM: resolves the full layer stack (vendor → org → workspace →
// project → user → draft) for a not-yet-saved draft body. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const body = (await req.json()) as AgentProfileDraftInput;
  const preview = buildPreview(
    body.agent_id,
    body.scope,
    body.scope_id ?? null,
    { workspaceId: body.workspace_id, projectId: body.project_id, userId: body.user_id },
    {
      promptPrepend: body.prompt_prepend ?? "",
      promptAppend: body.prompt_append ?? "",
      outputContractExtra: body.output_contract_extra ?? "",
    },
  );
  return Response.json({ ...preview, warnings: [] });
}
