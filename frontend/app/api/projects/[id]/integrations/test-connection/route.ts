import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Try a credential live, before it's saved — proxied to FastAPI
 * `POST /projects/{id}/integrations/test-connection`.
 *
 * The value in the request body is never written anywhere on the backend
 * either; it lives only for the one connector call this makes. See that
 * endpoint's docstring (shared/routers/project_scoped.py) for which
 * connector kinds support testing a personal credential today.
 */
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body: unknown = await req.json();
  return bffProxy(`/projects/${encodeURIComponent(id)}/integrations/test-connection`, {
    method: "POST",
    body,
  });
}
