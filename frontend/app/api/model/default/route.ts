import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Set the default offering — proxied to FastAPI `PUT /model/default`.
 *
 * Previously mutated the shared fixture PROVIDERS array, so choosing a default
 * held until the next reload and never reached the tenant's settings.
 */
export async function PUT(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/model/default", { method: "PUT", body });
}
