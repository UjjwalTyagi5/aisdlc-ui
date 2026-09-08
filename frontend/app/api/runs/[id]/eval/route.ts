import { type NextRequest } from "next/server";

import { forward } from "@/lib/bff/forward";

type P = { params: Promise<{ id: string }> };

/** A run's evaluation records, newest first. The backend takes no query parameters. */
export async function GET(req: NextRequest, { params }: P) {
  const { id } = await params;
  return forward(req, `/runs/${encodeURIComponent(id)}/eval`);
}
