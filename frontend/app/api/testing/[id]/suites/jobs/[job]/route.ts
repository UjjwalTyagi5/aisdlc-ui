import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

/** One suite job, with its progress and result. */
export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string; job: string }> }) {
  const { id, job } = await params;
  return forward(req, `/testing/${encodeURIComponent(id)}/suites/jobs/${encodeURIComponent(job)}`);
}
