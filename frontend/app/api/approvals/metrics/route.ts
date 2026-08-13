import { bffProxy } from "@/lib/bff/proxy";

/**
 * Approval-queue metrics — proxied to FastAPI `GET /approvals/metrics`.
 *
 * The seam comment here said "swap to bffProxy when backend exists". It does.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return bffProxy("/approvals/metrics");
}
