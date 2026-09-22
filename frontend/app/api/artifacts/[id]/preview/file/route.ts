import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { bearerForRequest } from "@/lib/bff/client";

const FASTAPI_BASE = process.env["FASTAPI_INTERNAL_URL"] ?? "http://localhost:8001";

/**
 * The bytes behind a `file` preview — a PDF or an image — for the document viewer to draw.
 *
 * Binary, so not `bffFetch` (which parses JSON); this mirrors `download/route.ts`. The backend
 * serves only PDFs and images here, with the media type named by the extension and `nosniff`;
 * those headers, and the `sandbox` policy it puts on an SVG, are passed through unchanged.
 * A non-200 answer (404 missing, 410 rejected) is relayed with its body, so the viewer can
 * say which it was.
 */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const jwt = await bearerForRequest(session);
  const res = await fetch(
    `${FASTAPI_BASE}/artifacts/${encodeURIComponent(id)}/preview/file`,
    { headers: { Authorization: `Bearer ${jwt}` } },
  );

  if (!res.ok) {
    const text = await res.text();
    return new Response(text, {
      status: res.status,
      headers: { "Content-Type": res.headers.get("content-type") ?? "application/json" },
    });
  }

  const headers: Record<string, string> = {
    "Content-Type": res.headers.get("content-type") ?? "application/octet-stream",
    "Content-Disposition": res.headers.get("content-disposition") ?? "inline",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
  };
  const csp = res.headers.get("content-security-policy");
  if (csp) headers["Content-Security-Policy"] = csp;
  return new Response(res.body, { headers });
}
