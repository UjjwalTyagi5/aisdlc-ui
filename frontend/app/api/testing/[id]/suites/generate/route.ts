import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

/** Start generating the unit, functional and API test case suites for a branch. */
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body: unknown = await req.json().catch(() => ({}));
  return forward(`/testing/${encodeURIComponent(id)}/suites/generate`, { method: "POST", body, status: 202 });
}
