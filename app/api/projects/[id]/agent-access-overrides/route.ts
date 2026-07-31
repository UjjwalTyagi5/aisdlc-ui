import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import {
  listOverridesForProject,
  removeOverride,
  setOverride,
} from "@/lib/mock/agent-access-override-fixtures";
import type { AgentAccessOverrideInput } from "@/lib/schemas/agent-access";
import type { Phase } from "@/lib/schemas/enums";

// DUMMY-DATA SEAM: reads/writes the shared OVERRIDES array directly. Mirrored
// in mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id } = await params;
  return Response.json(listOverridesForProject(id));
}

export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id } = await params;
  const body = (await req.json()) as AgentAccessOverrideInput;
  if (!body?.role || !body?.phase || !body?.involvement) {
    return Response.json(
      { code: "invalid_input", message: "role, phase and involvement are required" },
      { status: 422 },
    );
  }
  const created = setOverride(id, body.role, body.phase, body.involvement, session.user.name);
  return Response.json(created, { status: 201 });
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id } = await params;
  const role = req.nextUrl.searchParams.get("role");
  const phase = req.nextUrl.searchParams.get("phase");
  if (!role || !phase) {
    return Response.json({ code: "invalid_input", message: "role and phase are required" }, { status: 422 });
  }
  const ok = removeOverride(id, role, phase as Phase);
  if (!ok) return Response.json({ code: "not_found" }, { status: 404 });
  return new Response(null, { status: 204 });
}
