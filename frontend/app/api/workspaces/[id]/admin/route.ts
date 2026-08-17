import { notImplemented } from "@/lib/bff/not-implemented";

/**
 * Re-appoint a Business Unit's admin (PRD §15.2).
 *
 * NOT IMPLEMENTED BY THE BACKEND as a single act. FastAPI can grant `bu_admin`
 * to someone (`POST /workspaces/{id}/members`) and revoke it from someone else
 * (`DELETE /workspaces/{id}/members/{userId}`), but nothing performs the HANDOVER
 * — and doing it here as two calls is precisely what should not be improvised:
 * fail between them and the unit has two admins or none.
 *
 * The authorization note is worth keeping for whoever builds it. This is Org
 * Admin only, and it answers 403 rather than the 404 its sibling routes use: a
 * unit's own Admin can see their unit perfectly well, so there is nothing to
 * conceal — what they cannot do is choose their own replacement. The check is
 * org-wideness, not `workspace:manage`, which the sitting admin passes.
 *
 * BACKLOG: FastAPI `POST /workspaces/{id}/admin`, transactional.
 */
export async function POST() {
  return notImplemented("POST /workspaces/{id}/admin");
}
