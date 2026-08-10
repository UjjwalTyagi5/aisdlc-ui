import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { bearerForRequest } from "@/lib/bff/client";

const FASTAPI_BASE = process.env["FASTAPI_INTERNAL_URL"] ?? "http://localhost:8001";

/**
 * POST /api/artifacts/export-docx — render an artifact's markdown to a Word (.docx)
 * file and stream it back for download. Signed BFF proxy to FastAPI
 * `POST /artifacts/export-docx`; the browser sends { title, markdown } and gets the
 * binary .docx (mirrors the testing-download binary-streaming pattern).
 */
export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return new Response("unauthenticated", { status: 401 });

  const body = await req.text();
  const jwt = await bearerForRequest(session);
  const res = await fetch(`${FASTAPI_BASE}/artifacts/export-docx`, {
    method: "POST",
    headers: { Authorization: `Bearer ${jwt}`, "Content-Type": "application/json" },
    body,
  });
  if (!res.ok) {
    return new Response("Could not generate the Word document.", { status: res.status });
  }
  return new Response(res.body, {
    headers: {
      "Content-Type":
        res.headers.get("content-type") ??
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "Content-Disposition": res.headers.get("content-disposition") ?? 'attachment; filename="document.docx"',
      "Cache-Control": "no-store",
    },
  });
}
