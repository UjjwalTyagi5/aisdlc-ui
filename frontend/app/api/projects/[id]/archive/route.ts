import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Archive a project — proxied to FastAPI `POST /projects/{id}/archive`.
 *
 * THE ROLE FORK IS BACK, and it is the backend's now. A Project Admin archives
 * their own project and an Org Admin archives anything; a Business Unit Admin
 * archiving a project they do not run files a `project_archive` request routed to
 * the Org Admin instead. Ending someone else's delivery work is the kind of
 * decision the request lane exists for.
 *
 * The response shape is the same either way — the project comes back with
 * `archived` still false when a request was filed, and the client reads "sent for
 * approval" from that. That was the original contract here and it is worth keeping:
 * a second response shape would make every caller branch on which one it got.
 */
export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/projects/${encodeURIComponent(id)}/archive`, { method: "POST" });
}
