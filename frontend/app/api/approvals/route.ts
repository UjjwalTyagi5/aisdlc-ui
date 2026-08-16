import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { AGENT_OWNERSHIP, ROLE_META, ROLE_ORDER, type PlatformRole } from "@/lib/roles";
import type { Phase } from "@/lib/schemas/enums";

/**
 * Pending-gate queue — proxied to FastAPI `GET /approvals`.
 *
 * Gates are derived from `runs.gate_pending` server-side, so the queue can only
 * ever show runs the database says are actually paused. The scope filtering that
 * used to happen here (dropping gates outside the viewer's projects) is now done
 * in the backend, against real bindings rather than a fixture array.
 *
 * `waitingForRole` is added HERE, not by the backend, and that split is deliberate:
 * the phase→owning-role matrix is presentation (which of the twelve roles to name
 * in the UI), the backend owns `requiredPermission` (authorization). Keeping the
 * matrix in one place stops the two copies from disagreeing about who approves what.
 *
 * Which gates ROUTE to the viewer is still a separate, narrower question answered
 * in components/app/approval-queue.tsx. Scope decides what may be seen at all;
 * routing decides what is theirs to act on.
 */

/** First role that owns this phase's gate — the one the UI names as the approver. */
function owningRoleFor(phase: Phase): string {
  const owner = ROLE_ORDER.find((role: PlatformRole) => {
    const involvement = AGENT_OWNERSHIP[role][phase];
    return involvement === "owner" || involvement === "primary";
  });
  // No owner in the matrix means the Project Admin fallback approves it — the
  // same fallback the gate itself falls back to, so naming it is accurate.
  return owner ? ROLE_META[owner].label : ROLE_META.project_admin.label;
}

export async function GET(req: NextRequest) {
  const type = req.nextUrl.searchParams.get("type");
  const qs = type ? `?type=${encodeURIComponent(type)}` : "";
  const res = await bffProxy(`/approvals${qs}`);
  if (!res.ok) return res;

  const gates = (await res.json()) as Array<Record<string, unknown>>;
  return Response.json(
    gates.map((gate) => ({
      ...gate,
      waitingForRole: owningRoleFor(gate["phase"] as Phase),
    })),
  );
}
