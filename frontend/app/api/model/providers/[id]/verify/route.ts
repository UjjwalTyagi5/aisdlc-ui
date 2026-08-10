import { type NextRequest } from "next/server";

import { verifyModelProvider } from "@/lib/mock/model-fixtures";

// DUMMY-DATA SEAM: mutates the shared PROVIDERS array directly. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export async function POST(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const result = verifyModelProvider(id);
  if (!result) return Response.json({ code: "not_found" }, { status: 404 });
  return Response.json(result);
}
