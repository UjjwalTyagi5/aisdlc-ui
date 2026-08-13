import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Audit trail — proxied to FastAPI `GET /audit`.
 *
 * The audit trail is the most sensitive cross-scope surface on the platform: it
 * names who did what to which resource. Filtering it in this tier against a
 * fixture array was the weakest possible place for that decision; it now happens
 * in the backend under row-level security, where a row outside the caller's
 * tenant is not merely hidden but unreadable.
 */
export async function GET(req: NextRequest) {
  const from = req.nextUrl.searchParams;
  const to = new URLSearchParams();
  to.set("page", from.get("page") ?? "1");
  // Mirrors the previous 200-row ceiling — an unbounded page size on the audit
  // trail is a memory problem on a table that only ever grows.
  const pageSize = Math.min(Number(from.get("pageSize") ?? "20") || 20, 200);
  to.set("page_size", String(pageSize));

  const projectId = from.get("projectId");
  if (projectId) to.set("project_id", projectId);
  const action = from.get("action");
  if (action) to.set("action", action);

  return bffProxy(`/audit?${to.toString()}`);
}
