import { type NextRequest } from "next/server";

import { getAgentProfileSummary } from "@/lib/mock/agent-profile-fixtures";
import type { ProfileScope } from "@/lib/schemas/agent-profiles";

// DUMMY-DATA SEAM: resolves the real per-tier cascade (org → workspace →
// project → user), falling back up the chain when the requested tier has no
// active version of its own. `workspace_id`/`project_id`/`user_id` describe
// the full chain context so inheritance can walk past the requested tier.
// Mirrored in mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export const dynamic = "force-dynamic";

export function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const scope = (sp.get("scope") ?? "workspace") as ProfileScope;
  const scopeId = sp.get("scope_id");
  const agents = getAgentProfileSummary(scope, scopeId, {
    workspaceId: sp.get("workspace_id"),
    projectId: sp.get("project_id"),
    userId: sp.get("user_id"),
  });
  return Response.json({ agents });
}
