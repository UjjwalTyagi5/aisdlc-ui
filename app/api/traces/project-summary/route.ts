import { type NextRequest } from "next/server";

import { TRACES } from "@/lib/mock/trace-fixtures";
import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { canReadProject } from "@/lib/mock/access-scope";

// DUMMY-DATA SEAM: returns fixtures directly. When the Langfuse-backed live
// proxy lands, replace the body with: return bffProxy(`/traces/project-summary?${search}`).
//
// SCOPE FILTER: this endpoint takes an arbitrary project_id, so it is a direct
// read of one project's spend and token volume by id. An unauthorized id gets a
// zeroed summary rather than a 404 — the caller is a per-project widget, and the
// shape it expects is the summary, not an error to render.
export async function GET(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const projectId = req.nextUrl.searchParams.get("project_id") ?? "";
  const windowDays = Number(req.nextUrl.searchParams.get("window_days") ?? "7");
  const allowed = canReadProject(resolveSessionScope(session), projectId);
  const traces = allowed ? TRACES.filter((t) => t.projectId === projectId) : [];
  const inputTokens = traces.reduce((a, t) => a + t.cost.inputTokens, 0);
  const outputTokens = traces.reduce((a, t) => a + t.cost.outputTokens, 0);
  return Response.json({
    projectId,
    windowDays,
    totalCostUsd: Number(traces.reduce((a, t) => a + t.cost.usd, 0).toFixed(4)),
    inputTokens,
    outputTokens,
    totalTokens: inputTokens + outputTokens,
    generatedAt: new Date(Date.UTC(2026, 5, 17, 12, 0, 0)).toISOString(),
  });
}
