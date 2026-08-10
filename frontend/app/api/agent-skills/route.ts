import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";
import { bffProxy } from "@/lib/bff/proxy";
import { SkillDetail, SkillList } from "@/lib/schemas/agent-skills";

/** Merged vendor + custom skills (query: agent_id, scope, scope_id). */
export function GET(req: NextRequest) {
  const search = req.nextUrl.searchParams.toString();
  return bffProxy(search ? `/agent-skills?${search}` : "/agent-skills", {
    schema: SkillList,
  });
}

/**
 * Create a v1 custom skill. Mirrors the agent-profiles draft route: forwards
 * the raw upstream body on failure instead of just `err.details`, because the
 * backend's 422 lint-violation shape ({detail: {violations}}) doesn't match the
 * app-wide {code,message,details} ApiError envelope — `details` would be empty
 * and the violations lost. See ApiRequestError.rawBody in lib/api/client.ts and
 * getLintViolations in lib/api/agent-profiles.ts.
 */
export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const body: unknown = await req.json();
  try {
    const data = await bffFetch("/agent-skills", {
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
