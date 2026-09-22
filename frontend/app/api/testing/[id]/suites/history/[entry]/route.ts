import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

/** One history entry — a generation and its runs — to open in the page. */
export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string; entry: string }> }) {
  const { id, entry } = await params;
  return forward(req, `/testing/${encodeURIComponent(id)}/suites/history/${encodeURIComponent(entry)}`);
}
