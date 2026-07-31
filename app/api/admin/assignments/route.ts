import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { canManageBusinessUnit } from "@/lib/mock/access-scope";
import type { Session } from "@/lib/auth/types";

/**
 * Granting or revoking a role inside a Business Unit is the highest-leverage
 * write on the platform: it is how someone would give themselves access to a
 * unit they don't administer. Both verbs therefore check MANAGE on the target
 * unit before anything reaches the backend.
 *
 * A body with no `workspace_id` is left to the backend to reject — inventing a
 * target here would be guessing at the caller's intent.
 */
function deniedTarget(session: Session, body: unknown): boolean {
  const target =
    body && typeof body === "object"
      ? ((body as { workspace_id?: unknown }).workspace_id ?? null)
      : null;
  if (typeof target !== "string") return false;
  return !canManageBusinessUnit(resolveSessionScope(session), target);
}

export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const body: unknown = await req.json();
  if (deniedTarget(session, body)) {
    return Response.json({ code: "not_found", message: "not found" }, { status: 404 });
  }
  const data = await bffFetch("/admin/assignments", {
    session,
    method: "POST",
    body,
  });
  return Response.json(data);
}

export async function DELETE(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const body: unknown = await req.json();
  if (deniedTarget(session, body)) {
    return Response.json({ code: "not_found", message: "not found" }, { status: 404 });
  }
  const data = await bffFetch("/admin/assignments", {
    session,
    method: "DELETE",
    body,
  });
  return Response.json(data);
}
