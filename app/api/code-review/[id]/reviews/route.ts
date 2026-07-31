import { type NextRequest } from "next/server";

import { getSession } from "@/lib/auth/session";

// DUMMY-DATA SEAM: no review has been prepared for any fixture project yet, so
// an empty list is the correct default. When the backend code-review service
// lands, replace the body with bffFetch(`/code-review/${id}/reviews`, { session }).
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  await params;
  return Response.json([]);
}
