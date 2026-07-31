import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { getUserDetail } from "@/lib/mock/user-directory-fixtures";

// DUMMY-DATA SEAM: joins workspace-fixtures.ts + project-membership-fixtures.ts
// + custom-role-fixtures.ts directly. Mirrored in mocks/handlers.ts — see
// [[msw-dual-runtime-mutation-rule]].
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const detail = getUserDetail(id);
  if (!detail) return Response.json({ code: "not_found" }, { status: 404 });
  return Response.json(detail);
}
