import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { VerifyResult } from "@/lib/schemas/model";

export async function POST(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return bffProxy(`/model/providers/${encodeURIComponent(id)}/verify`, {
    method: "POST", schema: VerifyResult,
  });
}
