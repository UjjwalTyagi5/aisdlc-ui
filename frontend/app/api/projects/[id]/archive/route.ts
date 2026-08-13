import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Archive a project — proxied to FastAPI `POST /projects/{id}/archive`.
 *
 * ONE BRANCH IS LOST, and it is the interesting one. The fixture version forked
 * on the caller's role: a Project Admin or Org Admin archived directly, while a
 * Business Unit Admin's attempt instead opened a governance approval and left
 * `archived` false — the client inferred "sent for approval" from that.
 *
 * FastAPI archives, full stop. The request half depended on governance requests,
 * which the backend does not model (see app/api/governance-approvals/route.ts),
 * so keeping the fork would mean a BU Admin's click filing a request into an
 * in-memory array and reporting it as sent.
 *
 * BACKLOG: restore the request-instead-of-archive branch once governance
 * requests exist, and gate it in FastAPI rather than here.
 */
export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/projects/${encodeURIComponent(id)}/archive`, { method: "POST" });
}
