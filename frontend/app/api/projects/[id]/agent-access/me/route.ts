import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * The caller's own agent reach on this project — proxied to FastAPI
 * `GET /projects/{id}/agent-access/me`.
 *
 * WHY THE PAGE ASKS THE SERVER. The project overview and each agent's page used to
 * decide a padlock from the static role × agent table on the client, so an extra
 * agent granted from the Members page (or by an approved access request) stayed
 * locked: nothing on the client ever saw the grant. The API answers from the same
 * resolution its chat gates apply — person override, the person's extra agents, role
 * override, default — so what the page shows is what the API allows.
 *
 * Every frontend call goes through these BFF routes (lib/api/client.ts::API_BASE is
 * `/api`); a backend route without one is a 404 to the browser, and the page then
 * quietly falls back to the table — which is exactly how this looked "not fixed".
 */
export const dynamic = "force-dynamic";

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/projects/${encodeURIComponent(id)}/agent-access/me`);
}
