import { z } from "zod";

import { InvolvementLevel } from "./agent-access";
import { Phase } from "./enums";
import { MembershipStatus } from "./workspace";

/** One (scope, role) binding, resolved with enough context to render and
 *  link to it — see app/(app)/users/[id]/page.tsx. */
export const UserDetailBinding = z.object({
  scope: z.enum(["workspace", "project"]),
  id: z.string(),
  name: z.string(),
  /** A project binding's parent Business Unit — null for a workspace binding. */
  parentName: z.string().nullable(),
  role: z.string(),
  status: MembershipStatus,
});
export type UserDetailBinding = z.infer<typeof UserDetailBinding>;

export const RolePermissionSummary = z.object({
  id: z.string(),
  label: z.string(),
  grants: z.string().nullable(),
});
export type RolePermissionSummary = z.infer<typeof RolePermissionSummary>;

export const RoleAgentAccessRow = z.object({
  phase: Phase,
  label: z.string(),
  level: InvolvementLevel,
});
export type RoleAgentAccessRow = z.infer<typeof RoleAgentAccessRow>;

/** A distinct role this person holds somewhere, resolved to its permissions
 *  and agent-access levels — a built-in role via lib/roles.ts::ROLE_PERMISSIONS
 *  / AGENT_OWNERSHIP, or a custom role via its own CustomRole record. */
export const RoleSummary = z.object({
  role: z.string(),
  label: z.string(),
  isCustom: z.boolean(),
  tier: z.enum(["governance", "delivery"]).nullable(),
  permissions: z.array(RolePermissionSummary),
  /** Only phases with access above "none". */
  agentAccess: z.array(RoleAgentAccessRow),
});
export type RoleSummary = z.infer<typeof RoleSummary>;

/** GET /admin/users/:id response — everything the Users page's detail view
 *  needs in one call: who they are, every scope they belong to, and what
 *  each distinct role they hold actually grants. */
export const UserDetail = z.object({
  userId: z.string(),
  displayName: z.string(),
  email: z.string().nullable(),
  initials: z.string(),
  workspaceBindings: z.array(UserDetailBinding),
  projectBindings: z.array(UserDetailBinding),
  roleSummaries: z.array(RoleSummary),
});
export type UserDetail = z.infer<typeof UserDetail>;
