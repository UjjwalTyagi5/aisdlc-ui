import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

/** Start generating the unit, functional and API test case suites for a branch. */
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return forward(req, `/testing/${encodeURIComponent(id)}/suites/generate`, { method: "POST", withBody: true });
}
