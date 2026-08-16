import { notImplemented } from "@/lib/bff/not-implemented";

/**
 * Borrow a contributor from another Business Unit for one project
 * ([[cross-bu-contributor-loan]]).
 *
 * NOT IMPLEMENTED BY THE BACKEND. It files a governance request, which is not
 * modelled (see app/api/governance-approvals/route.ts), and approving it would
 * write a cross-BU grant, for which there is no table (see
 * app/api/admin/cross-bu-grants/route.ts).
 *
 * WHY THIS ENDPOINT EXISTS RATHER THAN THE GENERIC ONE, preserved for whoever
 * builds it: a cross-unit request is only meaningful with a project, a person and
 * a role in hand, and the one place all three are already known is the project's
 * Members screen. The generic raise form collects none of them — and the request
 * would be routed off a `workspaceId` the asker picked, when the only correct one
 * is the contributor's PARENT unit, which the server derives.
 *
 * BACKLOG: FastAPI `POST /projects/{id}/access-requests`.
 */
export async function POST() {
  return notImplemented("POST /projects/{id}/access-requests");
}
