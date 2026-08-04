import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { escalateGovernanceApproval } from "@/lib/mock/governance-approval-fixtures";
import type { RequestReasonInput } from "@/lib/schemas/governance-approval";

// DUMMY-DATA SEAM: mutates the shared fixture store. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
//
// Escalation is the REQUEST lane's fallback, and only that lane's (PRD §33.2:
// "None — it climbs until a tier can grant it"). Nothing here can be pointed at
// an agent sign-off: §44.5 makes the security and release sign-offs unwaivable
// "by any role, including the Organization Admin", so those never climb — they
// wait for the specialist who owns them.
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const body = (await req.json().catch(() => ({}))) as RequestReasonInput;

  const result = escalateGovernanceApproval(id, session.user.name, body.reason);
  if (result === undefined) return Response.json({ code: "not_found" }, { status: 404 });
  if (result === "top") {
    return Response.json(
      {
        code: "conflict",
        message:
          "This request is already with the Organization Admin — there is no tier above to escalate to.",
      },
      { status: 409 },
    );
  }
  return Response.json(result);
}
