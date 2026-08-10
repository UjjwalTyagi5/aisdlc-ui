import { getSession } from "@/lib/auth/session";
import { resolveSessionScope } from "@/lib/auth/access-scope";

// DUMMY-DATA SEAM: resolves the viewer's Business Unit / project scope from the
// membership fixtures (lib/mock/access-scope.ts). This is a critical-path route
// — the sidebar, every scoped list and every scope indicator depend on it — so
// it answers from the seam directly rather than relying on MSW interception.
// Mirrored in mocks/handlers.ts per [[msw-dual-runtime-mutation-rule]].
//
// When the backend resolves scope on the session, this becomes a passthrough of
// whatever the session already carries; the response SHAPE
// (lib/schemas/access-scope.ts) is the contract and does not change.
export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  return Response.json(resolveSessionScope(session));
}
