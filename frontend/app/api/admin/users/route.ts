import { getSession } from "@/lib/auth/session";
import { hasPermission } from "@/lib/auth/permissions";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import { scopeUserDirectory } from "@/lib/mock/user-directory-fixtures";

// DUMMY-DATA SEAM: reads the in-memory identity/membership stores directly.
// Mirrored in mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]].
//
// SCOPE — anyone who ADMINISTERS a Business Unit reads the organisation: the
// Organization Admin, and a Business Unit Admin who has to be able to find a
// person in another unit to borrow them. A Project Admin does not: their
// `member:manage` is about their own projects' rosters, so they see the unit
// they belong to ([[access-scope-rbac-layer]]).
//
// The containment is on the WRITE, and it holds independently: the membership
// endpoints check `canManageBusinessUnit`, so an org-wide read never becomes an
// org-wide edit.
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
