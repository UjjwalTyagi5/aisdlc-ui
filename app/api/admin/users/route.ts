import { getSession } from "@/lib/auth/session";
import { hasPermission } from "@/lib/auth/permissions";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { scopeUserDirectory } from "@/lib/mock/user-directory-fixtures";

// DUMMY-DATA SEAM: reads the in-memory identity/membership stores directly.
// Mirrored in mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
//
// SCOPE — the Organization Admin sees the organisation; everyone else sees the
// Business Unit they belong to ([[access-scope-rbac-layer]]). A unit admin has
// no standing over another unit's people, and a Project Admin's
// `member:manage` is about their own projects' rosters.
//
// Writing stays scoped independently: the membership endpoints check
// `canManageBusinessUnit`, so the read boundary is not the only thing holding.
//
// A Contributor gets nothing at all — the directory names every colleague's
// email and role, which is an administrator's view of the organisation.
export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });
  if (!hasPermission(session, "member:manage")) {
    return Response.json({ code: "forbidden", message: "forbidden" }, { status: 403 });
  }
  return Response.json(scopeUserDirectory(resolveSessionScope(session)));
}
