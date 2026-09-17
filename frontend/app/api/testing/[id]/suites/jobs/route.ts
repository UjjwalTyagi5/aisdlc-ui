import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

/** This project's suite jobs (generation and runs), newest first. */
export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return forward(req, `/testing/${encodeURIComponent(id)}/suites/jobs`, { withQuery: true });
}
