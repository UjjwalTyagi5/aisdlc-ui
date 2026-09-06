import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

type P = { params: Promise<{ projectId: string; stage: string }> };

export async function GET(req: NextRequest, { params }: P) {
  const { projectId, stage } = await params;
  return forward(req, `/artifact-versions/${encodeURIComponent(projectId)}/stages/${encodeURIComponent(stage)}/versions/published`);
}
