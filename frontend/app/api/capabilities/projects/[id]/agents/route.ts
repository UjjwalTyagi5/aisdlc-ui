import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * What a project's agents can reach — proxied to FastAPI
 * `GET /capabilities/projects/{id}/agents`.
 *
 * The fixture version derived this from the project's track roster and the
 * fixture MCP registry, so it listed tool servers against an `mcp_servers` table
 * holding none.
 */
export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/capabilities/projects/${encodeURIComponent(id)}/agents`);
}
