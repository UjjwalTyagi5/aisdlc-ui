import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

type P = { params: Promise<{ id: string; jobId: string }> };

/** Poll an in-progress evidence export. */
export async function GET(req: NextRequest, { params }: P) {
  const { id, jobId } = await params;
  return forward(
    req,
    `/runs/${encodeURIComponent(id)}/evidence/${encodeURIComponent(jobId)}/status`,
  );
}
