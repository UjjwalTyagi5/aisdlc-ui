import { notImplemented } from "@/lib/bff/not-implemented";

/**
 * One person's role on one project.
 *
 * NOT IMPLEMENTED BY THE BACKEND — see app/api/projects/[id]/members/route.ts.
 *
 * BACKLOG: FastAPI `PATCH/DELETE /projects/{id}/members/{membershipId}`.
 */
export async function PATCH() {
  return notImplemented("PATCH /projects/{id}/members/{membershipId}");
}

export async function DELETE() {
  return notImplemented("DELETE /projects/{id}/members/{membershipId}");
}
