import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";
import { bffProxy } from "@/lib/bff/proxy";
import { BusinessUnitTechStacks, TechStack } from "@/lib/schemas/tech-stacks";

/** A Business Unit's tech stacks (query: workspace_id). */
export function GET(req: NextRequest) {
  const search = req.nextUrl.searchParams.toString();
  return bffProxy(search ? `/tech-stacks?${search}` : "/tech-stacks", { schema: BusinessUnitTechStacks });
}

/**
 * Create a tech stack. Forwards the raw upstream body on failure, as the agent-skills route
 * does: the backend's 422 `{detail: {violations}}` does not fit the app-wide ApiError envelope,
 * and the editor needs the violations to put each reason on its field.
 */
export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const body: unknown = await req.json();
  try {
    const data = await bffFetch("/tech-stacks", { session, method: "POST", body, schema: TechStack });
    return Response.json(data, { status: 201 });
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
