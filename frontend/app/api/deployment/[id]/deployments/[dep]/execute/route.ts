import { type NextRequest } from "next/server";
import { ApiRequestError } from "@/lib/api/client";
import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

/**
 * THE STATUS MATTERS HERE. The gate refuses with 409 and a machine-readable code —
 * self_approval, not_approved, already_executed — and lib/api/client.ts unwraps
 * {detail: {code, message}} into something a person can read. Letting bffFetch's throw
 * escape would turn every one of those into an opaque 500.
 */
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string; dep: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id, dep } = await params;
  const body = await req.json().catch(() => ({}));
  try {
    return Response.json(await bffFetch(
      `/deployment/${encodeURIComponent(id)}/deployments/${encodeURIComponent(dep)}/execute`,
      { session, method: "POST", body },
    ));
  } catch (err) {
    if (err instanceof ApiRequestError) {
      return Response.json(err.details ?? { code: err.code, message: err.message },
        { status: err.status });
    }
    throw err;
  }
}
