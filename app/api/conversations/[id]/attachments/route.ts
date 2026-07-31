import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { bearerForRequest, FASTAPI_BASE } from "@/lib/bff/client";
import { bffProxy } from "@/lib/bff/proxy";

/**
 * Multipart upload passthrough. bffFetch is JSON-only, so we forward the raw
 * FormData to FastAPI ourselves with the BFF bearer (browser never sees it).
 * Content-Type is intentionally NOT set — fetch derives the multipart boundary
 * from the FormData body.
 */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) {
    return Response.json({ code: "unauthenticated" }, { status: 401 });
  }
  const { id } = await params;
  const form = await req.formData();
  const jwt = await bearerForRequest(session);

  const res = await fetch(
    `${FASTAPI_BASE}/conversations/${encodeURIComponent(id)}/attachments`,
    { method: "POST", headers: { Authorization: `Bearer ${jwt}` }, body: form },
  );
  const data: unknown = await res.json().catch(() => null);
  return Response.json(data, { status: res.status });
}

/** List a session's stored attachments. */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  return bffProxy(`/conversations/${encodeURIComponent(id)}/attachments`);
}
