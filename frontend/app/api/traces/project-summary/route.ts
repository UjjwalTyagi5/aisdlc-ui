import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * One project's spend and token volume — proxied to FastAPI
 * `GET /traces/project-summary`.
 *
 * This endpoint takes an arbitrary `project_id`, so it is a direct read of one
 * project's figures by id and has to be authorized on that id rather than on the
 * caller's default scope. The backend does that now; the fixture version
 * answered from `lib/mock/trace-fixtures` and, tellingly, stamped every response
 * with a hardcoded `generatedAt` of 17 June 2026.
 */
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const search = req.nextUrl.searchParams.toString();
  return bffProxy(`/traces/project-summary${search ? `?${search}` : ""}`);
}
