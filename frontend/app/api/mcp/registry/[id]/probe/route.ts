import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { McpTestResult } from "@/lib/schemas/mcp";

export async function POST(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return bffProxy(`/mcp/registry/${encodeURIComponent(id)}/probe`, {
    method: "POST",
    schema: McpTestResult,
  });
}
