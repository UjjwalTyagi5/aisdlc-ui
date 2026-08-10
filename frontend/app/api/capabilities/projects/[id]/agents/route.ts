import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { getProjectCapabilitiesData } from "@/lib/mock/capabilities-fixtures";

// DUMMY-DATA SEAM: derives capability data from the project's actual track
// roster + MCP registry. Mirrored in mocks/handlers.ts — see
// [[msw-dual-runtime-mutation-rule]].
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const data = getProjectCapabilitiesData(id);
  if (!data) return Response.json({ code: "not_found", message: "Project not found" }, { status: 404 });
  return Response.json(data);
}
