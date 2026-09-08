import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

type P = { params: Promise<{ id: string }> };

/**
 * Start the evidence ZIP build. POST with no body — the backend takes none, and
 * `withBody` is deliberately absent so an empty request is not turned into a parse
 * failure.
 */
export async function POST(req: NextRequest, { params }: P) {
  const { id } = await params;
  return forward(req, `/runs/${encodeURIComponent(id)}/evidence`, { method: "POST" });
}
