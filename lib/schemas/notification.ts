import { z } from "zod";

import { NotificationId, ProjectId, RunId } from "./ids";
import { Timestamp } from "./primitives";

/**
 * The `request_*` kinds cover a request's lifecycle (PRD FR-05's "permanent
 * record" has a notification counterpart at each transition).
 *
 * Kept as six distinct kinds rather than one `request_update` with a payload:
 * they reach different people for different reasons — `request_assigned` and
 * `request_approval_required` go to the approver, the other four to the
 * initiator — and a single kind would make "who should hear about this" a
 * runtime question rather than a property of the event.
 */
export const NotificationKind = z.enum([
  "hitl_pending",
  "run_failed",
  "run_completed",
  "budget_near_cap",
  "guardrail_blocked",
  "mention",
  "request_created",
  "request_assigned",
  "request_approval_required",
  "request_approved",
  "request_rejected",
  "request_escalated",
  /** A Contributor was onboarded into a Business Unit and is waiting for its
   *  admin to give them a role. Addressed to that unit's admins, never
   *  broadcast — it is an item of work, not news. */
  "member_awaiting_role",
]);
export type NotificationKind = z.infer<typeof NotificationKind>;

export const Notification = z.object({
  id: NotificationId,
  kind: NotificationKind,
  title: z.string(),
  body: z.string().optional(),
  projectId: ProjectId.optional(),
  runId: RunId.optional(),
  href: z.string().optional(),
  readAt: Timestamp.nullable(),
  createdAt: Timestamp,
});
export type Notification = z.infer<typeof Notification>;
