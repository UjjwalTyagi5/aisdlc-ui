import { z } from "zod";

import { AuditId, ProjectId, TenantId, UserId } from "./ids";
import { Timestamp } from "./primitives";

export const AuditEvent = z.object({
  id: AuditId,
  tenantId: TenantId,
  projectId: ProjectId.nullable(),
  /**
   * Resolved server-side per page, so the row can say "Dummy T1" where it used to
   * print a UUID. `.nullish()` because a backend that predates the resolution omits
   * it entirely, and because an event outside any project legitimately has none.
   */
  projectName: z.string().nullish(),
  /**
   * NOT the `AuditAction` enum. That enum is 14 values written from the spec; the
   * backend's vocabulary is open and already emits families it never listed —
   * `rbac.role.granted`, `rbac.custom_role.created`, `access.denied`. Validating
   * against the closed set made EVERY real row fail, so the Audit Trail rendered
   * "Couldn't load audit events / SCHEMA_MISMATCH" rather than the log.
   *
   * An audit view is exactly the wrong place to fail closed: refusing to display a
   * page because one action is unrecognised hides the other rows too, and a new
   * action type is a routine backend change. `AuditAction` stays as the KNOWN set,
   * which is what the filter dropdown and the colour maps are keyed on — both
   * already fall back for anything they do not recognise.
   */
  action: z.string(),
  actor: z.object({
    id: UserId.or(z.literal("system").or(z.literal("agent"))),
    name: z.string(),
  }),
  resource: z.object({
    type: z.string(),
    id: z.string(),
    // `.nullish()`, not `.optional()`: the backend sends an explicit `null` when a
    // resource has no display name, and `.optional()` accepts a MISSING key but
    // rejects a null one. Every row carries `"name": null`.
    name: z.string().nullish(),
  }),
  at: Timestamp,
  /** Truncated free-form payload shown in the row drawer. */
  detail: z.record(z.unknown()).nullish(),
  // Same null-vs-absent point as `resource.name` — every row sends `"ip": null`.
  ip: z.string().nullish(),
});
export type AuditEvent = z.infer<typeof AuditEvent>;
