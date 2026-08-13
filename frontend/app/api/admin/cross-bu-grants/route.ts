import { emptyList, notImplemented } from "@/lib/bff/not-implemented";

/**
 * Live cross-unit loans — contributors lent from one Business Unit to another
 * unit's project ([[cross-bu-contributor-loan]]).
 *
 * NOT IMPLEMENTED BY THE BACKEND. There is no cross-BU grant table: a loan is a
 * seat authorised in a project without the person's parent unit changing, and
 * `role_bindings` has no way to express "bound here, owned there".
 *
 * The read this served answered both directions at once — who of mine is working
 * elsewhere, and whose people are working here — because an admin needs both and
 * they are one fact seen from two sides. Worth keeping in view for whoever builds
 * it; today there are no loans, because there is nowhere to record one.
 *
 * BACKLOG: FastAPI `GET/DELETE /admin/cross-bu-grants`.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return emptyList();
}

export async function DELETE() {
  return notImplemented("DELETE /admin/cross-bu-grants");
}
