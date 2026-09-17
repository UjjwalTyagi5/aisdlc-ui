import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

/** This project's suite jobs (generation and runs), newest first. */
export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const qs = req.nextUrl.searchParams.toString();
  return forward(`/testing/${encodeURIComponent(id)}/suites/jobs${qs ? `?${qs}` : ""}`);
}
