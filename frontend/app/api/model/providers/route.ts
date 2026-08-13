import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Model provider connections (BYOK) — proxied to FastAPI `GET/POST /model/providers`.
 *
 * `?scope=all` and `?workspaceId=` are accepted but no longer narrow anything, and
 * that is the backend's deliberate model rather than an oversight here: provider
 * connections are TENANT-WIDE. The resolver that picks a model for a run filters by
 * tenant alone, so a list narrowed by workspace would show something different from
 * what agents can actually run on — which is exactly the bug the backend comment in
 * services/model_config.py::list_providers records, where "removed" connections
 * survived unseen because the resolver ignored workspace.
 *
 * Which models a unit or project may USE is a separate question, answered by the
 * grant cascade (/model/allowed/*), not by hiding rows from this list.
 *
 * The response carries no secret fields — ProviderOut omits the API key, and the
 * key itself lives in Key Vault or the encrypted secret store, never in the DB row.
 */
export const dynamic = "force-dynamic";

export async function GET(_req: NextRequest) {
  return bffProxy("/model/providers");
}

export async function POST(req: NextRequest) {
  const body = (await req.json()) as { provider?: string; display_name?: string };
  if (!body?.provider || !body?.display_name) {
    return Response.json(
      { code: "invalid_input", message: "provider and display_name are required" },
      { status: 422 },
    );
  }
  return bffProxy("/model/providers", { method: "POST", body });
}
