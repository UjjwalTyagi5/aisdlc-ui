import { type NextRequest } from "next/server";

import { listMessages } from "@/lib/mock/conversation-fixtures";

// DUMMY-DATA SEAM: returns the in-memory fixture store directly. When the
// backend conversations service lands, replace the body with bffProxy(...).

/** Transcript for an owned session. */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  return Response.json(listMessages(id));
}
