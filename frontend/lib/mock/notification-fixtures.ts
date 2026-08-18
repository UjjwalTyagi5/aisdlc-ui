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
 *
 * A ROLE ADDRESS ALSO NAMES A SCOPE, and until it did, `role: "bu_admin"` meant
 * EVERY Business Unit Admin in the organisation — so the Lending admin read
 * Payments' business. The role says WHO, the scope says WHICH of them, and only
 * the pair is deliverable. Mirrors `notifications.recipient_scope_id` in the
 * backend (migration 0022); the two must agree, because this store is what the
 * app talks to in mock mode and the real table is what it talks to otherwise.
 * The one exception is a role held at the organization: there is a single
 * Organization Admin queue, so it is addressed without a scope.
 *
 * MATCHED AGAINST BINDINGS, NOT THE ACTING ROLE. "The Payments admin" is a fact
 * about what someone holds, and `effectivePlatformRole` collapses to their most
 * senior one. A viewer is therefore described by the queues they hold — see
 * `notificationViewer` in `access-scope.ts`, which is where binding resolution
 * already lives.
 */
import type { Notification, NotificationKind } from "@/lib/schemas/notification";
import type { NotificationId } from "@/lib/schemas/ids";
import { ROLE_META, type PlatformRole } from "@/lib/roles";

/** Where a role queue lives. Mirrors `role_bindings.scope_kind` server-side. */
export type NotificationScopeKind = "business_unit" | "project";

export interface StoredNotification extends Notification {
  /** Delivered to this identity, when it is addressed to a person. */
  identityId: string | null;
  /** Delivered to whoever currently acts as this role, when it is a queue. */
  role: PlatformRole | null;
  /** Which unit or project's queue — null only for an organization-wide role. */
  scopeKind: NotificationScopeKind | null;
  scopeId: string | null;
}

/**
 * One queue a viewer holds: a role, and every scope id that role answers for.
 *
 * `covers` is pre-expanded rather than matched by walking the hierarchy at read
 * time, because containment runs BOTH ways and the store has no fixture graph to
 * walk. A unit binding covers the unit and the projects inside it (a unit admin
 * belongs to the queue for their own project); a project binding covers the
 * project and its parent unit (a project admin belongs to their unit's queue).
 * What no expansion reaches is a sibling unit, which is the entire point.
 */
export interface NotificationQueue {
  role: string;
  covers: string[];
}

export interface NotificationViewer {
  identityId: string | null;
  /** The role they are ACTING as — matches the unscoped organization queue. */
  actingRole: PlatformRole | null;
  /** Every queue they HOLD — matches scoped rows, whatever hat they wear. */
  queues: NotificationQueue[];
}

let nextId = 1;
const NOTIFICATIONS: StoredNotification[] = [];

/** Is this role held somewhere narrower than the whole organization? */
function needsScope(role: PlatformRole): boolean {
  return ROLE_META[role]?.scope !== "organization";
}

/**
 * Deliver one notification, or null when it could not be addressed.
 *
 * A role address with no scope is undeliverable rather than broadly deliverable:
 * dropping one notification is a smaller failure than putting one unit's business
 * in every other unit's bell. The backend's `emit` refuses the same case the same
 * way, for the same reason.
 */
export function emitNotification(input: {
  kind: NotificationKind;
  title: string;
  body?: string;
  href?: string;
  identityId?: string | null;
  role?: PlatformRole | null;
  scopeKind?: NotificationScopeKind | null;
  scopeId?: string | null;
}): StoredNotification | null {
  let role = input.role ?? null;
  const scopeId = input.scopeId ?? null;

  if (role && !scopeId && needsScope(role)) {
    console.error(
      `[notifications] not emitted — role address '${role}' has no scope ` +
        `(kind=${input.kind} title=${JSON.stringify(input.title)}); pass scopeId`,
    );
    // Still deliverable to the person it also named, if it named one. Drop only
    // the half that cannot be addressed.
    if (!input.identityId) return null;
    role = null;
  }

  const created: StoredNotification = {
    id: `ntf_${nextId++}` as NotificationId,
    kind: input.kind,
    title: input.title,
    body: input.body,
    href: input.href,
    readAt: null,
    createdAt: new Date().toISOString(),
    identityId: input.identityId ?? null,
    role,
    scopeKind: role ? (input.scopeKind ?? null) : null,
    scopeId: role ? scopeId : null,
  };
  NOTIFICATIONS.unshift(created);
  return created;
}

/**
 * What this viewer should see: anything addressed to them personally, plus
 * anything addressed to a role-and-scope they hold.
 *
 * Both, not either — an approver who also raises requests needs their own
 * outcomes and their queue in one list, which is what a bell is for.
 */
export function listNotifications(viewer: NotificationViewer): StoredNotification[] {
  return NOTIFICATIONS.filter((n) => {
    if (n.identityId !== null && n.identityId === viewer.identityId) return true;
    if (n.role === null) return false;
    // No scope: the organization's own queue, matched against the role they are
    // acting as. Org-wide standing does not come from a membership row, so a
    // queue match would miss it.
    if (n.scopeId === null) return n.role === viewer.actingRole;
    return viewer.queues.some((q) => q.role === n.role && q.covers.includes(n.scopeId!));
  }).slice(0, 50);
}

export function markNotificationsRead(viewer: NotificationViewer): number {
  const now = new Date().toISOString();
  let n = 0;
  // Bounded to exactly what the listing returns — you can only mark read what you
  // could see, so this cannot clear somebody else's queue.
  for (const item of listNotifications(viewer)) {
    if (item.readAt === null) {
      item.readAt = now;
      n++;
    }
  }
  return n;
}
