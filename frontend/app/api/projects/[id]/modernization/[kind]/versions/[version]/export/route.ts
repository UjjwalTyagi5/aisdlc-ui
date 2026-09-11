import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { bearerForRequest } from "@/lib/bff/client";

const FASTAPI_BASE = process.env["FASTAPI_INTERNAL_URL"] ?? "http://localhost:8001";

/**
 * Download one frozen version of a Track 3 brief or assessment as .docx or .pdf.
 *
 * Streams FastAPI `GET /projects/{id}/modernization/{kind}/versions/{version}/export`
 * — NOT `bffProxy`, which parses JSON. Same pattern as `app/api/artifacts/[id]/download`:
 * take the bearer, stream the body back, and pass a failure's status and body through
 * so its reason survives. The file is rendered from that version's own payload, so a
 * download of v2 is v2 even after v5 exists.
 */
const KINDS = new Set(["migration-intent", "discovery"]);
const FORMATS = new Set(["docx", "pdf"]);

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; kind: string; version: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id, kind, version } = await params;
  const format = req.nextUrl.searchParams.get("format") ?? "docx";
  if (!KINDS.has(kind) || !/^\d+$/.test(version) || !FORMATS.has(format)) {
    return Response.json({ code: "not_found" }, { status: 404 });
  }

  const jwt = await bearerForRequest(session);
  const res = await fetch(
    `${FASTAPI_BASE}/projects/${encodeURIComponent(id)}/modernization/${kind}/versions/${version}/export?format=${format}`,
    { headers: { Authorization: `Bearer ${jwt}` } },
  );
  if (!res.ok) {
    const text = await res.text();
    return new Response(text, {
      status: res.status,
      headers: { "Content-Type": res.headers.get("content-type") ?? "application/json" },
    });
  }
  // The filename is FastAPI's own (`<stem>-v<N>.<ext>`), never caller input.
  return new Response(res.body, {
    headers: {
      "Content-Type": res.headers.get("content-type") ?? "application/octet-stream",
      "Content-Disposition": res.headers.get("content-disposition") ?? "attachment",
      "Cache-Control": "no-store",
    },
  });
}
