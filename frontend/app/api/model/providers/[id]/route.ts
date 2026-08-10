import { type NextRequest } from "next/server";

import { deleteModelProvider, updateModelProvider } from "@/lib/mock/model-fixtures";

// DUMMY-DATA SEAM: mutates the shared PROVIDERS array directly. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export async function PATCH(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const body = (await req.json()) as {
    display_name?: string;
    enabled_models?: string[];
    api_key?: string;
    api_base?: string | null;
    rpm_limit?: number | null;
    tpm_limit?: number | null;
    cost_limit_usd?: number | null;
  };
  const updated = updateModelProvider(id, body);
  if (!updated) return Response.json({ code: "not_found" }, { status: 404 });
  return Response.json(updated);
}

export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const ok = deleteModelProvider(id);
  if (!ok) return Response.json({ code: "not_found" }, { status: 404 });
  return new Response(null, { status: 204 });
}
