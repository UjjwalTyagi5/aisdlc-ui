import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { setCuratedDisabled } from "@/lib/mock/capabilities-fixtures";

// DUMMY-DATA SEAM: mirrors mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; agentId: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id, agentId } = await params;
  const body = (await req.json()) as { disabled: string[] };
  const result = setCuratedDisabled(id, agentId, body.disabled ?? []);
  return Response.json(result);
}
