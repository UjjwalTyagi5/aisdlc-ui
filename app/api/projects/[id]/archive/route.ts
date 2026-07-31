import { type NextRequest } from "next/server";

import { requestOrArchiveProject } from "@/lib/mock/project-fixtures";
import { getSession } from "@/lib/auth/session";
import { effectivePlatformRole } from "@/lib/auth/effective-role";

// DUMMY-DATA SEAM: a Project Admin (owner) or Org Admin archives directly; a
// BU Admin's request instead opens a governance approval and the project's
// `archived` stays false in the response — the client infers "sent for
// approval" from that, no separate field needed. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const role = effectivePlatformRole(session);
  const result = requestOrArchiveProject(id, { role, displayName: session.user.name });
  if (!result) return Response.json({ code: "not_found", message: "not found" }, { status: 404 });
  return Response.json(result.project);
}
