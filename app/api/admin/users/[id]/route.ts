import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { hasPermission } from "@/lib/auth/permissions";
import { changeOrgAppointment } from "@/lib/mock/onboarding";
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

/**
 * Change someone's org-level appointment. Organization Admin only (`admin:*`),
 * matching onboarding: a Business Unit Admin assigns roles inside their unit
 * and never decides who belongs to the organisation or to which unit.
 */
export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  if (!hasPermission(session, "admin:*")) {
    return Response.json(
      { code: "forbidden", message: "Appointments are an Organization Admin action." },
      { status: 403 },
    );
  }

  const { id } = await params;
  const body = (await req.json().catch(() => ({}))) as {
    role?: string;
    workspaceId?: string | null;
  };
  const { status, body: payload } = changeOrgAppointment({
    userId: id,
    ...body,
    actorName: session.user?.name,
  });
  return Response.json(payload, { status });
}
