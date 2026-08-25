import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

export async function POST(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const body: unknown = await req.json();
  return bffProxy(`/model/providers/${encodeURIComponent(id)}/assign`, {
    method: "POST",
    body,
  });
}
