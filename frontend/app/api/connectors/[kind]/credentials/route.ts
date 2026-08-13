import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * Onboard a connector credential — proxied to FastAPI
 * `POST /connectors/{kind}/credentials`.
 *
 * This route USED to proxy, was converted to a fixture write when the frontend
 * ran standalone, and goes back now. Its own comment recorded why it changed and
 * what that cost: "the secret itself is read and discarded — nothing echoes it
 * back, and a fixture store is not somewhere to keep one". Which meant the flow
 * reported `status: "valid"` for a credential nobody had checked and nothing had
 * stored, so a Business Unit Admin who pasted a wrong key learned about it later,
 * from a broken agent run.
 *
 * The grant check that ran here is gone with `lib/mock/connector-grants` — there
 * is no grant table for it to read (see app/api/connectors/grants/route.ts).
 * FastAPI gates this on `connector:manage`, so onboarding is still bounded by
 * permission; what is not enforced today is the narrower "your Org Admin
 * permitted this KIND for your unit" rule.
 *
 * BACKLOG: reinstate the per-unit kind check once connector grants exist.
 */
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest, { params }: { params: Promise<{ kind: string }> }) {
  const { kind } = await params;
  const body: unknown = await req.json().catch(() => ({}));
  return bffProxy(`/connectors/${encodeURIComponent(kind)}/credentials`, {
    method: "POST",
    body,
  });
}
