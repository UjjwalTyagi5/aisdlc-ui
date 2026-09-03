import { type NextRequest } from "next/server";
import { ApiRequestError } from "@/lib/api/client";
import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  const { id } = await params;
  const pending = req.nextUrl.searchParams.get("pending_only") === "true";
  try {
    return Response.json(await bffFetch(
      `/deployment/${encodeURIComponent(id)}/deployments${pending ? "?pending_only=true" : ""}`,
      { session },
    ));
  } catch (err) {
    if (err instanceof ApiRequestError) {
      return Response.json(err.details ?? { code: err.code, message: err.message },
        { status: err.status });
    }
    throw err;
  }
}
