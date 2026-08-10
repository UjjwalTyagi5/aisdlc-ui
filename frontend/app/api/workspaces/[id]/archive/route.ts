import { type NextRequest } from "next/server";

import { archiveWorkspace } from "@/lib/mock/workspace-fixtures";
import { getSession } from "@/lib/auth/session";

// DUMMY-DATA SEAM: mirrors mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id } = await params;
  const ws = archiveWorkspace(id);
  if (!ws) return Response.json({ code: "not_found", message: "not found" }, { status: 404 });
  return Response.json(ws);
}
