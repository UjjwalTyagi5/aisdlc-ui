import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { bearerForRequest } from "@/lib/bff/client";

const FASTAPI_BASE = process.env["FASTAPI_INTERNAL_URL"] ?? "http://localhost:8001";

/** Proxies the Testing agent's QA report HTML for inline display. */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string; session: string }> },
) {
  const session = await getSession();
  if (!session) return new Response("unauthenticated", { status: 401 });
  const { session: sid } = await params;
  const jwt = await bearerForRequest(session);
  const uid = encodeURIComponent(session.user.id);
  const url = `${FASTAPI_BASE}/sdlc/agent/testing/qa_report/${encodeURIComponent(sid)}?user_id=${uid}`;
  const res = await fetch(url, { headers: { Authorization: `Bearer ${jwt}` } });
  if (!res.ok) return new Response("No QA report for this run yet.", { status: res.status });
  const html = await res.text();
  return new Response(html, {
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
  });
}
