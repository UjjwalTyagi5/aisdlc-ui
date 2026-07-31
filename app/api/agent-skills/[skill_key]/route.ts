import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";
import { bffProxy } from "@/lib/bff/proxy";
import { SkillDeleteResult, SkillDetail } from "@/lib/schemas/agent-skills";

/**
 * Publish a new active version of a custom skill. Same raw-body 422 passthrough
 * as the create route — a lint failure uses the {detail: {violations}} shape.
 * See lib/api/client.ts (ApiRequestError.rawBody) and lib/api/agent-profiles.ts
 * (getLintViolations).
 */
export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ skill_key: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { skill_key: skillKey } = await params;
  const body: unknown = await req.json();
  try {
    const data = await bffFetch(`/agent-skills/${encodeURIComponent(skillKey)}`, {
      session,
      method: "PUT",
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

/** Soft-delete a custom skill (query: agent_id, scope, scope_id). */
export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ skill_key: string }> },
) {
  const { skill_key: skillKey } = await params;
  const search = req.nextUrl.searchParams.toString();
  const base = `/agent-skills/${encodeURIComponent(skillKey)}`;
  return bffProxy(search ? `${base}?${search}` : base, {
    method: "DELETE",
    schema: SkillDeleteResult,
  });
}
