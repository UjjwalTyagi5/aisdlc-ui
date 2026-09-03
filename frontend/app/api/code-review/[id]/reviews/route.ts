import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * Past reviews for a project, newest first — what the "Past reviews" switcher lists
 * and what the page auto-opens on load.
 *
 * Was a DUMMY-DATA SEAM returning a hardcoded `[]` long after the backend route it
 * stands in front of (`GET /code-review/{id}/reviews`) started answering with real
 * rows. Nothing about the failure looked like a stub: reviews really were being
 * persisted, the sibling `reviews/[runId]` route really did fetch them, and the page
 * rendered a perfectly ordinary "No review yet" — so the review history looked lost
 * rather than never asked for. The backend access log is what settled it: this
 * request never appeared there at all.
 */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const data = await bffFetch(`/code-review/${encodeURIComponent(id)}/reviews`, { session });
  return Response.json(data);
}
