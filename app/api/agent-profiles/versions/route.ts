import { type NextRequest } from "next/server";

import { listVersions } from "@/lib/mock/agent-profile-fixtures";
import type { ProfileScope } from "@/lib/schemas/agent-profiles";

// DUMMY-DATA SEAM: mirrors mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const agentId = sp.get("agent_id");
  const scope = (sp.get("scope") ?? "workspace") as ProfileScope;
  const scopeId = sp.get("scope_id");
  if (!agentId) {
    return Response.json({ code: "invalid_input", message: "agent_id is required" }, { status: 422 });
  }
  return Response.json({ versions: listVersions(agentId, scope, scopeId) });
}
