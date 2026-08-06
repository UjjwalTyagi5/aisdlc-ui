import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { hasPermission } from "@/lib/auth/permissions";
import { onboardIntoOrganization, type OnboardingInput } from "@/lib/mock/onboarding";

// DUMMY-DATA SEAM: the whole transaction lives in lib/mock/onboarding.ts so
// this handler and its MSW twin can never drift — see that file, and
// [[msw-dual-runtime-mutation-rule]].
//
// Admitting someone to the organisation is the Organization Admin's act:
// `admin:*` is the only permission that passes, so a Business Unit Admin
// (who holds `member:manage` and assigns roles inside their own unit) cannot
// bring in someone new.
export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  if (!hasPermission(session, "admin:*")) {
    return Response.json(
      { code: "forbidden", message: "Onboarding is an Organization Admin action." },
      { status: 403 },
    );
  }

  const body = (await req.json()) as OnboardingInput;
  // `actorName` comes from the session, never the body — it is the "raised by"
  // line on the request the unit's admin will answer.
  const { status, body: payload } = onboardIntoOrganization({
    ...body,
    actorName: session.user?.name,
  });
  return Response.json(payload, { status });
}
