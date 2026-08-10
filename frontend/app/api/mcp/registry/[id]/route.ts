import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { McpServer } from "@/lib/schemas/mcp";

export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return bffProxy(`/mcp/registry/${encodeURIComponent(id)}`, { schema: McpServer });
}

export async function PUT(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const body: unknown = await req.json();
  return bffProxy(`/mcp/registry/${encodeURIComponent(id)}`, {
    method: "PUT",
    body,
    schema: McpServer,
  });
}

export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return bffProxy(`/mcp/registry/${encodeURIComponent(id)}`, { method: "DELETE" });
}
