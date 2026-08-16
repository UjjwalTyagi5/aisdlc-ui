import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { ModelProvider } from "@/lib/schemas/model";

export async function PATCH(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const body: unknown = await req.json();
  return bffProxy(`/model/providers/${encodeURIComponent(id)}`, {
    method: "PATCH", body, schema: ModelProvider,
  });
}

export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return bffProxy(`/model/providers/${encodeURIComponent(id)}`, { method: "DELETE" });
}
