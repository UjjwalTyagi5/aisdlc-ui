import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { bearerForRequest } from "@/lib/bff/client";

const FASTAPI_BASE = process.env["FASTAPI_INTERNAL_URL"] ?? "http://localhost:8001";

/** List generated files for a Testing run session (JSON). */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string; session: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { session: sid } = await params;
  const jwt = await bearerForRequest(session);
  const uid = encodeURIComponent(session.user.id);
  const url = `${FASTAPI_BASE}/sdlc/agent/testing/files/${encodeURIComponent(sid)}?user_id=${uid}`;
  const res = await fetch(url, { headers: { Authorization: `Bearer ${jwt}` } });
  if (!res.ok) return Response.json({ files: [] });
  return Response.json(await res.json().catch(() => ({ files: [] })));
}
