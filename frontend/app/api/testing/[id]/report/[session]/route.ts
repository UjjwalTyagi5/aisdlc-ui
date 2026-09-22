import { type NextRequest } from "next/server";

import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/** The Testing agent's run report for one session (see `run_report.py`). */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string; session: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { session: sid } = await params;
  const uid = encodeURIComponent(session.user.id);
  return Response.json(
    await bffFetch(`/sdlc/agent/testing/report/${encodeURIComponent(sid)}?user_id=${uid}`, { session }),
  );
}
