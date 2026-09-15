import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * The whole filtered trail — proxied to FastAPI `GET /audit/export`.
 *
 * Separate from the list route because it is a different ACT, not a bigger page.
 * PRD §34.9 makes export an audited event, and the backend writes that record in the
 * same request that returns the rows — so the file cannot be obtained without the
 * record existing. That is only true while the data comes from here; the moment a
 * client builds its own export from the list endpoint, the record stops being a
 * precondition and becomes a request.
 *
 * Every filter is forwarded. An export that covered more than the screen it was taken
 * from would be a quiet over-disclosure, and one that covered less would be a file
 * that lies about being the audit log.
 */
export async function GET(req: NextRequest) {
  const from = req.nextUrl.searchParams;
  const to = new URLSearchParams();
  to.set("fmt", from.get("fmt") ?? "csv");

  for (const [param, backend] of [
    ["projectId", "project_id"],
    ["workspaceId", "workspace_id"],
    ["actor", "actor"],
    ["action", "action"],
    ["q", "q"],
  ] as const) {
    const v = from.get(param);
    if (v) to.set(backend, v);
  }

  return bffProxy(`/audit/export?${to.toString()}`);
}
