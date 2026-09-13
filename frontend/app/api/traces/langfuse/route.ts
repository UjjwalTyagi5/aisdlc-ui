import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Where this viewer may go in Langfuse — proxied to FastAPI `GET /traces/langfuse`.
 *
 * The backend answers with the projects they can actually OPEN, not the ones they can
 * read traces for in this product. The two are different systems and only agree by
 * coincidence today; a link that lands on an access-denied page makes the product look
 * broken rather than correctly restrictive, so the filtering belongs where the Langfuse
 * grants live.
 */
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const search = req.nextUrl.searchParams.toString();
  return bffProxy(`/traces/langfuse${search ? `?${search}` : ""}`);
}
