import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Transcript for one session — proxied to FastAPI
 * `GET /conversations/{id}/messages`.
 *
 * The backend checks the session's owner before returning a word of it, which is
 * the check that matters here: a transcript is the most quotable thing on the
 * platform, and the fixture store returned any id to any caller.
 */
export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return bffProxy(`/conversations/${encodeURIComponent(id)}/messages`);
}
