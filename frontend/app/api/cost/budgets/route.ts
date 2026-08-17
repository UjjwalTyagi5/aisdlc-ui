import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

/**
 * The budget hub — proxied to FastAPI `GET/PUT /cost/budgets`.
 *
 * Previously a DUMMY-DATA SEAM over `lib/mock/cost-fixtures`, with writes landing
 * in `patchWorkspace` — an in-memory mutation that reverted on the next reload
 * and never reached `organizations.monthly_budget_usd`.
 *
 * The backend gates the read on `cost:view` and the write on `workspace:manage`,
 * which is the same split this handler approximated with `canManageBusinessUnit`
 * — except the backend checks it against real bindings, and checks it for org and
 * project scopes too, which the fixture version silently ignored.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return bffProxy("/cost/budgets");
}

export async function PUT(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/cost/budgets", { method: "PUT", body });
}
