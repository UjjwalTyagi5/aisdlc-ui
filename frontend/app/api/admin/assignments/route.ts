import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Granting or revoking a role inside a Business Unit is the highest-leverage
 * write on the platform: it is how someone would give themselves access to a unit
 * they don't administer, and granting `org_admin` there escalates further still.
 *
 * The MANAGE-on-target check that used to live here now lives in FastAPI
 * (`assert_can_write_workspace`), because this tier being the only enforcement was
 * the problem: the backend took `workspace_id` from the body and never compared it
 * to what the caller actually administers. Revoke is guarded identically — stripping
 * a sibling unit's admin of their role is a denial of service on that unit, not a
 * lesser act than granting one.
 */
export async function POST(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/admin/assignments", { method: "POST", body });
}

export async function DELETE(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/admin/assignments", { method: "DELETE", body });
}
