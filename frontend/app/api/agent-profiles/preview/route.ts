import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Resolve a not-yet-saved draft against the layers beneath it — proxied to
 * FastAPI `POST /agent-profiles/preview`.
 *
 * The backend resolves fewer layers than the fixture version claimed to: a
 * draft payload carries no workspace id, so a project-scoped draft's workspace
 * layer is omitted (a deviation the backend documents at the call site). The
 * preview is therefore thinner than it was and, unlike before, every layer in it
 * is one that exists.
 */
export async function POST(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/agent-profiles/preview", { method: "POST", body });
}
