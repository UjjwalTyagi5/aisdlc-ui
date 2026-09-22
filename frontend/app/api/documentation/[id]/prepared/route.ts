import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

/**
 * The docs workspace already prepared for this project. `getPreparedDocs` called this path
 * with no route behind it, so in the browser it was a 404 and the page never kept its
 * workspace across a refresh — the unit test mocked the client and could not see that.
 */
export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return forward(req, `/documentation/${encodeURIComponent(id)}/prepared`);
}
