import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * What this project may use, and whose credential is behind each one — proxied to
 * FastAPI `GET/PUT /projects/{id}/integrations`.
 *
 * The permitted set comes from the GRANT to the project's unit, not from what the
 * organisation onboarded: a project can only use what its unit was given, which is
 * what makes the cascade a cascade. Configuring a credential for something the unit
 * was never granted is a 403 with `not_granted`, not a silent no-op.
 *
 * CREDENTIALS ARE KEYED ON THE OWNER as well as the project. A credential
 * authenticates a PERSON against a tool — a repo bot, a board account, a database
 * role are each somebody's — and keyed on the project alone the second contributor to
 * configure Jira would silently replace the first, with neither able to tell.
 *
 * No secret comes back, ever: `hasSecret` is the only part of one a UI should show.
 */
export const dynamic = "force-dynamic";

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/projects/${encodeURIComponent(id)}/integrations`);
}

export async function PUT(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body: unknown = await req.json();
  return bffProxy(`/projects/${encodeURIComponent(id)}/integrations`, { method: "PUT", body });
}
