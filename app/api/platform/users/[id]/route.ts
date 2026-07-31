import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";

export async function PATCH(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id } = await ctx.params;
  const body: unknown = await req.json();
  try {
    await bffFetch(`/platform/users/${id}`, { session, method: "PATCH", body });
    return new Response(null, { status: 204 });
  } catch (err) {
    if (err instanceof ApiRequestError) {
      return Response.json(err.details ?? { message: err.message }, { status: err.status });
    }
    throw err;
  }
}
