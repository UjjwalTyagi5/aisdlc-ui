import { type NextRequest } from "next/server";

import { setProjectArchived } from "@/lib/mock/project-fixtures";
import { getSession } from "@/lib/auth/session";

// DUMMY-DATA SEAM: mirrors mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const project = setProjectArchived(id, false);
  if (!project) return Response.json({ code: "not_found", message: "not found" }, { status: 404 });
  return Response.json(project);
}
