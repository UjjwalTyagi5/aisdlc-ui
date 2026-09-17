import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

/** This project's testing history: each generation with the runs of its test cases. */
export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return forward(req, `/testing/${encodeURIComponent(id)}/suites/history`, { withQuery: true });
}
