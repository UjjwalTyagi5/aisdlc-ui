import { type NextRequest } from "next/server";

import { ApiRequestError } from "@/lib/api/client";
import { getSession } from "@/lib/auth/session";
import { bffFetch } from "@/lib/bff/client";

/**
 * Platform-tier org provisioning proxy (Phase 4). POST {display_name, slug,
 * admin_email, admin_password} → FastAPI POST /platform/organizations (platform:*
 * gated). Passes the backend status through so 409 (slug taken) / 422 (invalid)
 * reach the browser instead of collapsing to 500.
 */
export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const body: unknown = await req.json();
  try {
    const data = await bffFetch("/platform/organizations", {
      session,
      method: "POST",
      body,
    });
    return Response.json(data, { status: 201 });
  } catch (err) {
    if (err instanceof ApiRequestError) {
      return Response.json(
        err.details ?? { message: err.message },
        { status: err.status },
      );
    }
    throw err;
  }
}
