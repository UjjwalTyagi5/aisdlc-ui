import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Live cross-unit loans — proxied to FastAPI `GET/DELETE /admin/cross-bu-grants`.
 *
 * A loan records a SEAT, not a membership: the whole point is that the person's home
 * unit does not change. A row in `role_bindings` would make them a member of the
 * borrowing unit and lose the fact that somebody else still owns them — which is why
 * this needed its own table and could not exist until it had one.
 *
 * The read answers both directions at once — who of mine is working elsewhere, and
 * whose people are working here — because an admin needs both and they are one fact
 * seen from two sides. `lentByYou` says which side the viewer is on.
 *
 * Ending a loan is the LENDING unit's admin alone. The borrowing unit can take the
 * person off the project like any other member; ending the loan is the lender's,
 * because it is their person and their headcount.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return bffProxy("/admin/cross-bu-grants");
}

export async function DELETE(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/admin/cross-bu-grants", { method: "DELETE", body });
}
