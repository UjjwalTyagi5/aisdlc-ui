import { getSession } from "@/lib/auth/session";
import { sessionIdentityId } from "@/lib/auth/access-scope";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import {
  listNotifications,
  markNotificationsRead,
} from "@/lib/mock/notification-fixtures";

// DUMMY-DATA SEAM: reads the in-memory notification store. Mirrored in
// mocks/handlers.ts — see [[msw-dual-runtime-mutation-rule]]: the store is
// written by the governance-approval transitions, which run in whichever
// runtime served the mutation, so the read must run there too.
//
// SCOPE: a notification is addressed to an identity or to a role, and the
// listing intersects both against the caller. There is no "all notifications"
// view — an unaddressed list would hand a contributor the Org Admin's queue.
export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  return Response.json(
    listNotifications(sessionIdentityId(session), effectivePlatformRole(session)),
  );
}

/** Mark everything currently addressed to this viewer as read. */
export async function POST() {
  const session = await getSession();
  if (!session) return Response.json({ code: "unauthenticated" }, { status: 401 });

  const marked = markNotificationsRead(
    sessionIdentityId(session),
    effectivePlatformRole(session),
  );
  return Response.json({ marked });
}
