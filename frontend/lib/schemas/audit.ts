import { z } from "zod";

import { AuditId, ProjectId, TenantId, UserId } from "./ids";
import { AuditAction } from "./enums";
import { Timestamp } from "./primitives";

export const AuditEvent = z.object({
  id: AuditId,
  tenantId: TenantId,
  projectId: ProjectId.nullable(),
  action: AuditAction,
  actor: z.object({
    id: UserId.or(z.literal("system").or(z.literal("agent"))),
    name: z.string(),
  }),
  resource: z.object({
    type: z.string(),
    id: z.string(),
    name: z.string().optional(),
  }),
  at: Timestamp,
  /** Truncated free-form payload shown in the row drawer. */
  detail: z.record(z.unknown()).optional(),
  ip: z.string().optional(),
});
export type AuditEvent = z.infer<typeof AuditEvent>;
