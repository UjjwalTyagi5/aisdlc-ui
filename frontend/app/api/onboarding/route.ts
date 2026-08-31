import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Admit someone to the organisation — proxied to FastAPI `POST /onboarding`.
 *
 * THREE ACTS IN ONE TRANSACTION: create the account, bind them to the unit, and raise
 * the `role_assignment` request that tells the unit's admin they owe this person a
 * job. The third is why this could not exist before — governance requests had no
 * backend, so the flow's whole point was missing and `lib/mock/onboarding` faked all
 * of it.
 *
 * Without that third act somebody lands in a unit and nobody is told to give them a
 * role: they sit on the `artifact:view` floor, able to sign in and do nothing, with no
 * record of why. The request IS the record, and it closes when a role is actually
 * assigned rather than by being approved.
 *
 * Only a Contributor generates one — a Business Unit Admin was given their job by this
 * very act, and so does somebody a unit admin onboards, because that caller names the
 * role in the same request. The response says which happened via `roleRequestId`.
 *
 * TWO CALLERS. An Organization Admin admits someone to the organisation and picks one
 * of the two org-level roles. A Business Unit Admin staffs a unit they administer and
 * picks the working role directly; the backend checks WHICH unit with
 * assert_can_write_workspace and answers 404 for any other.
 *
 * Nothing is decided here — this proxies. The role and scope checks are the backend's
 * and are the real gate, not the dialog's: a picker is a convenience, and a request
 * naming `developer` from a stale client has to be refused for the same reason the
 * picker doesn't offer it.
 */
export async function POST(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/onboarding", { method: "POST", body });
}
