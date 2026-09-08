import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

type P = { params: Promise<{ projectId: string; runId: string }> };

export async function GET(req: NextRequest, { params }: P) {
  const { projectId, runId } = await params;
  return forward(
    req,
    `/artifact-versions/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/evidence`,
  );
}
