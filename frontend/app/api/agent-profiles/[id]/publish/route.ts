import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Activate a version — proxied to FastAPI `POST /agent-profiles/{id}/publish`.
 *
 * The tier-ownership check that ran here is gone, and the rule it enforced is
 * still enforced: FastAPI gates publish on `workspace:manage`. It had to go
 * regardless — it read the version's scope from `lib/mock/agent-profile-fixtures`,
 * so with the fixtures out of the data path it would have 404'd every real
 * version before the backend ever saw the call.
 */
export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/agent-profiles/${encodeURIComponent(id)}/publish`, { method: "POST" });
}
