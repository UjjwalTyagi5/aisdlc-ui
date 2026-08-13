import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Propose a change to an Agent Studio default you don't own — proxied to FastAPI
 * `POST /agent-profiles/{id}/propose`.
 *
 * The counterpart to `/publish` for someone who is not the tier's owner. It files
 * an `agent_default_org|workspace|project` request; approving it publishes exactly
 * the draft version this id names.
 *
 * The body the client used to send (agent label, workspace name, project name) is
 * gone: the backend reads all of it off the profile row and the session. Those
 * fields decided WHO the request routed to and WHAT approving would publish, which
 * is precisely the set a client should not be supplying.
 */
export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/agent-profiles/${encodeURIComponent(id)}/propose`, { method: "POST" });
}
