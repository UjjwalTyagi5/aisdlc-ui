import { notImplemented } from "@/lib/bff/not-implemented";

/**
 * A Business Unit Admin asking for more budget than they may set themselves.
 *
 * NOT IMPLEMENTED BY THE BACKEND. This route's entire job is to file a
 * governance request routed to the Org Admin, and those are not modelled — see
 * app/api/governance-approvals/route.ts.
 *
 * This is the other half of the budget cascade noted on the parent workspace
 * route: a unit's Admin may set the FIRST cap directly, and changing one that
 * already exists comes here instead. With this half missing, the cascade has no
 * escalation path, which is why the parent route no longer refuses the change on
 * the grounds that it should be requested here.
 *
 * BACKLOG: FastAPI `POST /workspaces/{id}/budget-increase-request`.
 */
export async function POST() {
  return notImplemented("POST /workspaces/{id}/budget-increase-request");
}
