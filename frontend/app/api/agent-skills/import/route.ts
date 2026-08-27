import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";
import { SkillDetail } from "@/lib/schemas/agent-skills";

/**
 * Import a Skill through the backend's prompt-injection/credential/provenance
 * screens. Mirrors the create route's raw-body 422 passthrough (lint
 * violations, CREDENTIAL_DETECTED, SOURCE_NOT_ALLOWED all need their real
 * detail shape, not the generic {code,message,details} ApiError envelope) —
 * see lib/bff/client.ts's ApiRequestError.rawBody and getLintViolations in
 * lib/api/agent-profiles.ts.
 */
export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const body: unknown = await req.json();
  try {
    const data = await bffFetch("/agent-skills/import", {
      session,
      method: "POST",
      body,
      schema: SkillDetail,
    });
    return Response.json(data);
  } catch (err) {
    if (err instanceof ApiRequestError) {
      return Response.json(
        err.rawBody ?? err.details ?? { code: err.code, message: err.message },
        { status: err.status },
      );
    }
    throw err;
  }
}
