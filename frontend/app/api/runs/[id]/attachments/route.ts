import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { bearerForRequest, FASTAPI_BASE } from "@/lib/bff/client";
import { bffProxy } from "@/lib/bff/proxy";

/**
 * Files attached to an Orchestrator run, proxied to FastAPI `/runs/{id}/attachments`.
 *
 * NOT the sibling `/conversations/{id}/attachments` route, even though an Orchestrator
 * run's conversation id IS its run id. That endpoint authorises through the
 * conversation row, which the Orchestrator socket only creates on the FIRST TURN —
 * so attaching a file before typing anything would 404 against a run that genuinely
 * exists. The run route authorises through the same tenant-and-scope chokepoint the
 * run's other reads use, and holds from the moment the run row does.
 *
 * Multipart is forwarded by hand because `bffFetch` is JSON-only. Content-Type is
 * deliberately NOT set — fetch derives the multipart boundary from the FormData body,
 * and setting it manually produces a boundary that does not match the body.
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
    `${FASTAPI_BASE}/runs/${encodeURIComponent(id)}/attachments`,
    { method: "POST", headers: { Authorization: `Bearer ${jwt}` }, body: form },
  );
  // The status is forwarded rather than flattened. A rejected file type is a 400 with
  // a readable reason, and turning it into a 200 or a bare 500 would leave the composer
  // unable to tell the user which file it refused and why.
  const data: unknown = await res.json().catch(() => null);
  return Response.json(data, { status: res.status });
}

/** What this caller has already attached to the run — the chips on a reopened chat. */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  return bffProxy(`/runs/${encodeURIComponent(id)}/attachments`);
}
