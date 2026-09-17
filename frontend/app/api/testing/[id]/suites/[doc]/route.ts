import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

/** The cases in a stored test case workbook — what a run of it executes. */
export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string; doc: string }> }) {
  const { id, doc } = await params;
  return forward(`/testing/${encodeURIComponent(id)}/suites/${encodeURIComponent(doc)}`);
}
