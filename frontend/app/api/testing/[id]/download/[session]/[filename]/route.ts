import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { bearerForRequest } from "@/lib/bff/client";

const FASTAPI_BASE = process.env["FASTAPI_INTERNAL_URL"] ?? "http://localhost:8001";

/** Streams a generated Testing file (e.g. the reports zip) for download. */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string; session: string; filename: string }> },
) {
  const session = await getSession();
  if (!session) return new Response("unauthenticated", { status: 401 });
  const { session: sid, filename } = await params;
  const jwt = await bearerForRequest(session);
  const uid = encodeURIComponent(session.user.id);
  const url = `${FASTAPI_BASE}/sdlc/agent/testing/download/${uid}/${encodeURIComponent(sid)}/${encodeURIComponent(filename)}`;
  const res = await fetch(url, { headers: { Authorization: `Bearer ${jwt}` } });
  if (!res.ok) return new Response("File not found", { status: res.status });
  return new Response(res.body, {
    headers: {
      "Content-Type": res.headers.get("content-type") ?? "application/octet-stream",
      "Content-Disposition": `attachment; filename="${filename}"`,
      "Cache-Control": "no-store",
    },
  });
}
