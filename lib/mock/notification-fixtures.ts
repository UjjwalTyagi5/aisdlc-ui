/**
 * Notification store — in-memory, same mutable-array pattern as the other
 * fixture stores, server-safe so both the Next route handlers and the MSW
 * handlers can reach it ([[msw-dual-runtime-mutation-rule]]).
 *
 * Created for the request lifecycle (PRD FR-05). Before this, the bell was a
 * counter with an illustrative dropdown and nothing behind it — there was
 * literally nowhere for "request approved" to be delivered to.
 *
 * ADDRESSED, NOT BROADCAST. Every notification names an audience: an identity
 * (the initiator) or a role (whoever holds the queue it landed in). A store
 * that kept one global list would show a Developer the Org Admin's escalations,
 * which is both noise and a scope leak.
 */
import type { Notification, NotificationKind } from "@/lib/schemas/notification";
import type { NotificationId } from "@/lib/schemas/ids";
import type { PlatformRole } from "@/lib/roles";

export interface StoredNotification extends Notification {
  /** Delivered to this identity, when it is addressed to a person. */
  identityId: string | null;
  /** Delivered to whoever currently acts as this role, when it is a queue. */
  role: PlatformRole | null;
}

let nextId = 1;
const NOTIFICATIONS: StoredNotification[] = [];

export function emitNotification(input: {
  kind: NotificationKind;
  title: string;
  body?: string;
  href?: string;
  identityId?: string | null;
  role?: PlatformRole | null;
}): StoredNotification {
  const created: StoredNotification = {
    id: `ntf_${nextId++}` as NotificationId,
    kind: input.kind,
    title: input.title,
    body: input.body,
    href: input.href,
    readAt: null,
    createdAt: new Date().toISOString(),
    identityId: input.identityId ?? null,
    role: input.role ?? null,
  };
  NOTIFICATIONS.unshift(created);
  return created;
}

/**
 * What this viewer should see: anything addressed to them personally, plus
 * anything addressed to the role they are acting as.
 *
 * Both, not either — an approver who also raised requests needs their own
 * outcomes and their queue in one list, which is what a bell is for.
 */
export function listNotifications(
  identityId: string | null,
  role: PlatformRole | null,
): StoredNotification[] {
  return NOTIFICATIONS.filter(
    (n) =>
      (n.identityId !== null && n.identityId === identityId) ||
      (n.role !== null && n.role === role),
  ).slice(0, 50);
}

export function markNotificationsRead(identityId: string | null, role: PlatformRole | null): number {
  const now = new Date().toISOString();
  let n = 0;
  for (const item of listNotifications(identityId, role)) {
    if (item.readAt === null) {
      item.readAt = now;
      n++;
    }
  }
  return n;
}
