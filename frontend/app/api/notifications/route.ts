import { emptyList, notImplemented } from "@/lib/bff/not-implemented";

/**
 * The notification bell.
 *
 * NOT IMPLEMENTED BY THE BACKEND — there is no notifications table and no
 * endpoint. `lib/mock/notification-fixtures` was written BY the governance-
 * approval transitions, so with those gone (see
 * app/api/governance-approvals/route.ts) nothing would write to it even if it
 * stayed: every notification the bell showed was seeded, not earned.
 *
 * That is also why the unread badge read "1" on a fresh, empty organisation.
 *
 * The scoping note is worth preserving for whoever builds this: a notification is
 * addressed to an identity OR to a role, and the listing must intersect both
 * against the caller. There is no "all notifications" view — an unaddressed list
 * hands a contributor the Org Admin's queue.
 *
 * BACKLOG: FastAPI `GET /notifications` + `POST /notifications/read`.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return emptyList();
}

export async function POST() {
  return notImplemented("POST /notifications/read");
}
