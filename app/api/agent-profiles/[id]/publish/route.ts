import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { canPublishAtTier } from "@/lib/governance";
import { publishVersion, versionScope } from "@/lib/mock/agent-profile-fixtures";

// DUMMY-DATA SEAM: mirrors mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;

  // Publishing was authenticated-only: any signed-in person could activate a
  // version at ANY tier, so the cascade's ownership was a UI convention rather
  // than a rule. Checked here now, against the draft's own recorded scope —
  // the tier is the version's, not something the caller gets to assert.
  const scope = versionScope(id);
  if (!scope) return Response.json({ code: "not_found" }, { status: 404 });
  if (!canPublishAtTier(effectivePlatformRole(session), scope)) {
    return Response.json(
      { code: "forbidden", message: "You don't own this tier's defaults." },
      { status: 403 },
    );
  }

  const published = publishVersion(id, session.user.name);
  if (!published) return Response.json({ code: "not_found" }, { status: 404 });
  return Response.json(published);
}
