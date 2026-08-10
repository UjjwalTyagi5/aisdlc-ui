import { type NextRequest } from "next/server";

import { TRACES, listItem } from "@/lib/mock/trace-fixtures";
import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { filterByProject } from "@/lib/mock/access-scope";

// DUMMY-DATA SEAM: returns fixtures directly. When the Langfuse-backed live
// proxy lands, replace the body with: return bffProxy(`/traces?${search}`).
//
// SCOPE FILTER: a trace carries prompts, tool calls and model output from a
// specific project — the richest cross-project payload on the platform, and the
// one place where "read-only visibility" would still expose another team's
// requirements verbatim.
export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const scope = resolveSessionScope(session);
  const agent = req.nextUrl.searchParams.get("agent");
  const project = req.nextUrl.searchParams.get("project");
  const status = req.nextUrl.searchParams.get("status");
  let traces = filterByProject(scope, TRACES, (t) => t.projectId);
  if (agent) traces = traces.filter((t) => t.agentType === agent);
  if (project) traces = traces.filter((t) => t.projectId === project);
  if (status) traces = traces.filter((t) => t.status === status);
  return Response.json(traces.map(listItem));
}
