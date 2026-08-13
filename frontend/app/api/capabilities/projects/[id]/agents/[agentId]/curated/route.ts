import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Turn a curated capability off for one agent — proxied to FastAPI
 * `PUT /capabilities/projects/{id}/agents/{agentId}/curated`.
 */
export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; agentId: string }> },
) {
  const { id, agentId } = await params;
  const body: unknown = await req.json();
  return bffProxy(
    `/capabilities/projects/${encodeURIComponent(id)}/agents/${encodeURIComponent(agentId)}/curated`,
    { method: "PUT", body },
  );
}
