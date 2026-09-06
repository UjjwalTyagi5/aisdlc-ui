import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

type P = { params: Promise<{ projectId: string; stage: string; version: string }> };

export async function POST(req: NextRequest, { params }: P) {
  const { projectId, stage, version } = await params;
  return forward(req, `/artifact-versions/${encodeURIComponent(projectId)}/stages/${encodeURIComponent(stage)}/versions/${encodeURIComponent(version)}/reject`, {
    method: "POST",
    withBody: true,
  });
}
