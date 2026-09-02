import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { bearerForRequest } from "@/lib/bff/client";

const FASTAPI_BASE = process.env["FASTAPI_INTERNAL_URL"] ?? "http://localhost:8001";

/**
 * Stream a stored artifact's bytes — the ONE authorised download path.
 *
 * NOT `bffFetch`: that parses JSON and validates against a Zod schema, which is right
 * for every other artifact route and wrong for a .docx. This mirrors the Testing
 * agent's download route (`app/api/testing/[id]/download/...`), the existing pattern
 * for proxying binary through the BFF — take the bearer, stream the body back.
 *
 * WHAT THIS REPLACED. The Requirements agent used to expose
 * `GET /sdlc/agent/requirement/download/{filename}` reading a flat, process-wide
 * `outputs/` directory. Traversal was guarded there, but nothing was tenant-scoped: the
 * handler took only `(filename: str)` and so could not check a tenant even in
 * principle, while the documents were written under fixed names (`outputs/brd.docx`).
 * One tenant's BRD overwrote another's, and whichever was on disk was served.
 *
 * Here the identifier is an artifact id, and FastAPI resolves it through a join on
 * `Run.tenant_id` — an id belonging to another tenant is a 404, not a download.
 *
 * THE UPSTREAM STATUS AND BODY ARE FORWARDED, not flattened to a generic error. The
 * three non-200 answers each mean something different to whoever clicked Download:
 *   404 + a message — the artifact predates blob storage; re-run the agent
 *   503             — blob storage is not configured on this deployment
 *   404 "not found" — no such artifact for this tenant
 * Collapsing them into "File not found" (as the Testing route does) would tell a user
 * to go looking for a missing file when the real answer is "an admin has not configured
 * storage".
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
    `${FASTAPI_BASE}/artifacts/${encodeURIComponent(id)}/download`,
    { headers: { Authorization: `Bearer ${jwt}` } },
  );

  if (!res.ok) {
    // Pass the upstream JSON straight through so the reason survives — see above.
    const text = await res.text();
    return new Response(text, {
      status: res.status,
      headers: { "Content-Type": res.headers.get("content-type") ?? "application/json" },
    });
  }

  // Content-Disposition comes from FastAPI, whose filename is the LEAF of the stored
  // blob path — already sanitised by artifact_store on the way in, never a value the
  // caller supplied, so it cannot be used to inject a header here.
  return new Response(res.body, {
    headers: {
      "Content-Type": res.headers.get("content-type") ?? "application/octet-stream",
      "Content-Disposition":
        res.headers.get("content-disposition") ?? "attachment",
      "Cache-Control": "no-store",
    },
  });
}
