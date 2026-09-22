import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/** Any document as the page can show it — a workbook's sheets, or a document's text.
 *
 * Same-origin for the same reason as `page/`: the app's Content Security Policy refuses a
 * fetch to the backend's origin. A 404 (no view for this kind of file) and a 410 (rejected)
 * are answers the page shows, so they are relayed with their status, not as a 500. */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  try {
    return Response.json(await bffFetch(`/artifacts/${encodeURIComponent(id)}/preview`, { session }));
  } catch (e) {
    if (e instanceof ApiRequestError) {
      return Response.json({ code: e.code, message: e.message }, { status: e.status });
    }
    throw e;
  }
}
