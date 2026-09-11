import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Track 3 — the repositories the project's Azure DevOps or GitHub connection can see,
 * for the Pull legacy code dialog. Proxied to FastAPI
 * `GET /projects/{id}/modernization/legacy-code/repositories`.
 */
const STAGES = new Set(["requirements_modernization", "discovery"]);

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const stage = req.nextUrl.searchParams.get("stage") ?? "requirements_modernization";
  if (!STAGES.has(stage)) return Response.json({ code: "not_found" }, { status: 404 });
  const adoProject = req.nextUrl.searchParams.get("ado_project") ?? "";
  const query = new URLSearchParams({ stage });
  if (adoProject) query.set("ado_project", adoProject);
  return bffProxy(
    `/projects/${encodeURIComponent(id)}/modernization/legacy-code/repositories?${query.toString()}`,
  );
}
