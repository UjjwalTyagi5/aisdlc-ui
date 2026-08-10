import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

// Live: proxies the FastAPI traces_router detail endpoint. The backend tags traces
// with run_id and enforces tenant scoping, so lookup is authoritative server-side.
export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return bffProxy(`/traces/${encodeURIComponent(id)}`);
}
