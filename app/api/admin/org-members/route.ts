import { bffFetch } from "@/lib/bff/client";
import { getSession } from "@/lib/auth/session";

export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  return Response.json(await bffFetch("/admin/org-members", { session }));
}
