import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

/** Run the suite in a stored test case workbook; the job files the report. */
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string; doc: string }> }) {
  const { id, doc } = await params;
  return forward(req, `/testing/${encodeURIComponent(id)}/suites/${encodeURIComponent(doc)}/run`, { method: "POST", withBody: true });
}
