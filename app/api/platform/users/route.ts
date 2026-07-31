import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";

export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const body: unknown = await req.json();
  try {
    const data = await bffFetch("/platform/users", { session, method: "POST", body });
    return Response.json(data, { status: 201 });
  } catch (err) {
    if (err instanceof ApiRequestError) {
      return Response.json(err.details ?? { message: err.message }, { status: err.status });
    }
    throw err;
  }
}
