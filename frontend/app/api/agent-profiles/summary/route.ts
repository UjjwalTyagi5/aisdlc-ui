import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Agent Studio's per-tier summary — proxied to FastAPI
 * `GET /agent-profiles/summary`.
 *
 * ONE BEHAVIOUR IS LOST WITH THE FIXTURES, and it is worth naming rather than
 * discovering. The fixture version walked the cascade: asked about a tier with no
 * active version of its own, it fell back up the chain (project → workspace → org
 * → vendor) and reported what would actually apply. The backend answers only for
 * the tier it was asked about, so a tier that has customised nothing now comes
 * back empty instead of showing its inherited layer.
 *
 * That is a real gap, but it is a gap in the honest direction — an empty tier
 * reads as "nothing set here", which is true, where the fixture chain-walk was
 * synthesising an answer from prompt layers no database held.
 *
 * `workspace_id` / `project_id` / `user_id` are forwarded unchanged. FastAPI
 * ignores query parameters it does not declare, so they cost nothing and stay
 * in place for the day the backend walks the chain itself.
 *
 * BACKLOG: cascade fallback in FastAPI `GET /agent-profiles/summary`.
 */
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const sp = new URLSearchParams(req.nextUrl.searchParams);
  if (!sp.get("scope")) sp.set("scope", "workspace");
  return bffProxy(`/agent-profiles/summary?${sp.toString()}`);
}
