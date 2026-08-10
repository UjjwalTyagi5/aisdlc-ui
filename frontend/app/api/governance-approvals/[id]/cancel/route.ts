import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { sessionIdentityId } from "@/lib/auth/access-scope";
import { cancelGovernanceApproval } from "@/lib/mock/governance-approval-fixtures";
import type { RequestReasonInput } from "@/lib/schemas/governance-approval";

// DUMMY-DATA SEAM: mutates the shared fixture store. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
//
// Withdrawal belongs to the INITIATOR, which is why identity is checked here
// and not just permission: a Business Unit Admin holds every permission a
// contributor does, and must still not be able to withdraw the contributor's
// request. Cancelling someone else's request would be a decision wearing
// another name — rejection is the decision that exists for that.
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const body = (await req.json().catch(() => ({}))) as RequestReasonInput;

  const result = cancelGovernanceApproval(
    id,
    session.user.name,
    sessionIdentityId(session),
    body.reason,
  );
  if (result === undefined) return Response.json({ code: "not_found" }, { status: 404 });
  if (result === "forbidden") {
    return Response.json(
      {
        code: "forbidden",
        message: "Only the person who raised a request can withdraw it, and only while it is open.",
      },
      { status: 403 },
    );
  }
  return Response.json(result);
}
