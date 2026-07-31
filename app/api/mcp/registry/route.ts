import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { McpServer } from "@/lib/schemas/mcp";
import { listMcpServers } from "@/lib/mock/mcp-fixtures";

// DUMMY-DATA SEAM: returns the fixture registry directly. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]]. When a real MCP
// registry service lands, replace with bffProxy(...) as before.
export const dynamic = "force-dynamic";

export function GET(req: NextRequest) {
  const activeOnly = req.nextUrl.searchParams.get("active_only") === "true";
  return Response.json(listMcpServers(activeOnly));
}

export async function POST(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/mcp/registry", { method: "POST", body, schema: McpServer });
}
