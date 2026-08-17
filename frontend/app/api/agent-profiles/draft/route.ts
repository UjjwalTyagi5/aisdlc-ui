import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Save a draft profile — proxied to FastAPI `POST /agent-profiles/draft`.
 *
 * The field-length lint that ran here is the backend's (`lint_profile_fields`),
 * and its 422 body carries the same `{detail: {violations}}` shape
 * `getLintViolations()` in lib/api/agent-profiles.ts already reads — which is why
 * the fixture version reproduced that shape by hand. One copy of the caps now,
 * rather than two that can drift apart into a draft this tier accepts and the
 * backend rejects.
 */
export async function POST(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/agent-profiles/draft", { method: "POST", body });
}
