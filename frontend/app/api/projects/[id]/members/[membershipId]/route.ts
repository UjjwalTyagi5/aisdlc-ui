import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

type Params = Promise<{ id: string; membershipId: string }>;

/**
 * One person's role on one project — proxied to FastAPI
 * `PATCH/DELETE /projects/{id}/members/{membershipId}`.
 *
 * The backend guards the write on the PROJECT, not just the permission:
 * `member:manage` says the caller may manage members somewhere, and a Project Admin
 * passing a sibling project's id would otherwise staff a roster that is not theirs.
 * It also matches the membership id against this project, so an id from elsewhere
 * cannot be edited by pairing it with a project the caller does run.
 */
export async function PATCH(req: NextRequest, { params }: { params: Params }) {
  const { id, membershipId } = await params;
  const body: unknown = await req.json();
  return bffProxy(
    `/projects/${encodeURIComponent(id)}/members/${encodeURIComponent(membershipId)}`,
    { method: "PATCH", body },
  );
}

export async function DELETE(_req: NextRequest, { params }: { params: Params }) {
  const { id, membershipId } = await params;
  return bffProxy(
    `/projects/${encodeURIComponent(id)}/members/${encodeURIComponent(membershipId)}`,
    { method: "DELETE" },
  );
}
