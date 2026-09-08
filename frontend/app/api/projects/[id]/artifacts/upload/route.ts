import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { bearerForRequest, FASTAPI_BASE } from "@/lib/bff/client";

/**
 * Multipart upload passthrough for a project document.
 *
 * NOT `forward()`, and not `bffFetch`: both send JSON. The raw FormData is passed
 * through so fetch derives the multipart boundary itself — setting Content-Type here
 * would send a boundary that does not match the body and FastAPI would reject the
 * parts. Same shape as the chat-attachment proxy, deliberately.
 *
 * The browser never sees the bearer; it is minted here from the session.
 *
 * WITHOUT THIS FILE THE FEATURE IS INVISIBLE. The browser calls `/api/...` and never
 * reaches FastAPI, so a backend route with no handler here returns Next's own 404 —
 * which reads as a backend fault and is not one. That shipped once already;
 * `__tests__/bff/every-api-path-has-a-proxy.test.ts` now fails when it happens.
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
    `${FASTAPI_BASE}/projects/${encodeURIComponent(id)}/artifacts/upload`,
    { method: "POST", headers: { Authorization: `Bearer ${jwt}` }, body: form },
  );
  // The backend's own body and status, not a flattened one: a 400 naming the
  // extension, a 422 naming the stage and a 403 are three different things the user
  // has to act on differently.
  const data: unknown = await res.json().catch(() => null);
  return Response.json(data, { status: res.status });
}
