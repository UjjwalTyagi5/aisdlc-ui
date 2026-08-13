import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * The organization's model catalogue policy — proxied to FastAPI
 * `GET/PUT /model/allowed/org`.
 *
 * The org-admin gate moved to the backend. It is a ROLE check there too, not a
 * permission one: a Business Unit Admin holds `model:manage` for their own unit,
 * so gating on the permission would let them widen what the whole organization
 * permits. That distinction was previously enforced only in this handler.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return bffProxy("/model/allowed/org");
}

export async function PUT(req: NextRequest) {
  const body = (await req.json()) as { entries?: unknown[] };
  return bffProxy("/model/allowed/org", {
    method: "PUT",
    body: { entries: body.entries ?? [] },
  });
}
