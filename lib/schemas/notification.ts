import { z } from "zod";

import { NotificationId, ProjectId, RunId } from "./ids";
import { Timestamp } from "./primitives";

export const NotificationKind = z.enum([
  "hitl_pending",
  "run_failed",
  "run_completed",
  "budget_near_cap",
  "guardrail_blocked",
  "mention",
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
