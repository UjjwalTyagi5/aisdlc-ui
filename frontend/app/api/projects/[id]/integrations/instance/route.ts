import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Pin WHICH instance this project's integration talks to — proxied to FastAPI
 * `PUT /projects/{id}/integrations/instance`.
 *
 * Separate from the credential route next door because the two are governed
 * differently: a credential is the caller's own identity and every delivery
 * role sets theirs, while the instance is where that identity gets SENT, which
 * FastAPI restricts to whoever administers the project
 * (`assert_can_administer_project`). Nothing is enforced here — this file only
 * forwards; the authority check lives on the backend where it cannot be
 * bypassed by calling the API directly.
 */
export const dynamic = "force-dynamic";

export async function PUT(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body: unknown = await req.json();
  return bffProxy(`/projects/${encodeURIComponent(id)}/integrations/instance`, {
    method: "PUT",
    body,
  });
}
