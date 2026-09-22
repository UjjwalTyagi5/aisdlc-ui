import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";
import { bffProxy } from "@/lib/bff/proxy";
import { TechStack, TechStackDeleted } from "@/lib/schemas/tech-stacks";

type Ctx = { params: Promise<{ id: string }> };

/** Edit a tech stack — the raw 422 body is forwarded so `violations` reach the editor. */
export async function PATCH(req: NextRequest, { params }: Ctx) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const { id } = await params;
  const body: unknown = await req.json();
  try {
    const data = await bffFetch(`/tech-stacks/${encodeURIComponent(id)}`, {
      session, method: "PATCH", body, schema: TechStack,
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

export async function DELETE(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return bffProxy(`/tech-stacks/${encodeURIComponent(id)}`, { method: "DELETE", schema: TechStackDeleted });
}
