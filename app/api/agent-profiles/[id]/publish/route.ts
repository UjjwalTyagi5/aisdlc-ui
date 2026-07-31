import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { publishVersion } from "@/lib/mock/agent-profile-fixtures";

// DUMMY-DATA SEAM: mirrors mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const published = publishVersion(id, session.user.name);
  if (!published) return Response.json({ code: "not_found" }, { status: 404 });
  return Response.json(published);
}
