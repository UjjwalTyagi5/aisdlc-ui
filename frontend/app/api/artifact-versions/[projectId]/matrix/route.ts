import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

type P = { params: Promise<{ projectId: string }> };

export async function GET(req: NextRequest, { params }: P) {
  const { projectId } = await params;
  return forward(req, `/artifact-versions/${encodeURIComponent(projectId)}/matrix`);
}
