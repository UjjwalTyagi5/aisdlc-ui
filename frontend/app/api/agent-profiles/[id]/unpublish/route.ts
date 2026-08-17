import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Stand a version down — proxied to FastAPI
 * `POST /agent-profiles/{id}/unpublish`.
 */
export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/agent-profiles/${encodeURIComponent(id)}/unpublish`, { method: "POST" });
}
