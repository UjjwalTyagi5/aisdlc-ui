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

// ─── The org-wide people directory (GET /admin/users) ────────────────────────

/** The org-level appointment — what the Organization Admin decided about a
 *  person, as opposed to what they do inside a unit. See lib/roles.ts's
 *  ORG_ASSIGNABLE_ROLES. */
export const OrgRole = z.enum(["org_admin", "bu_admin", "contributor"]);
export type OrgRole = z.infer<typeof OrgRole>;

/** One role held inside one scope, flattened for the directory table. */
export const DirectoryBinding = z.object({
  scope: z.enum(["business_unit", "project"]),
  id: z.string(),
  name: z.string(),
  /** For a project binding, the Business Unit it sits in — this is what
   *  decides whether the viewer may edit it. */
  businessUnitId: z.string().nullable(),
  role: z.string(),
  status: MembershipStatus,
});
export type DirectoryBinding = z.infer<typeof DirectoryBinding>;

/**
 * One person, as the whole organisation sees them.
 *
 * Deliberately NOT filtered per viewer: a Business Unit Admin sees every row,
 * because "who else is in this organisation" is a fair question and answering
 * it with only their own unit made the directory look like the unit's member
 * list. What varies by viewer is what they may CHANGE, and that is derived
 * client-side from `businessUnitId` against the viewer's managed units — one
 * rule, applied to a full list, rather than a list that quietly differs.
 */
export const DirectoryEntry = z.object({
  /** SSO subject — the id the detail route and every member API key off. */
  userId: z.string(),
  identityId: z.string(),
  displayName: z.string(),
  email: z.string().nullable(),
  initials: z.string(),
  orgRole: OrgRole,
  /**
   * WHAT THIS PERSON IS, in the unit they belong to — the role their Business
   * Unit Admin assigned, or the `contributor` placeholder while nobody has.
   *
   * Distinct from `orgRole`, which only ever holds the Organization Admin's two
   * answers. Before this existed the directory printed `orgRole` as the
   * person's role and left it reading "Contributor" for someone who had been a
   * Developer for a month — the assignment landed in the project column and the
   * one column claiming to say what they are never changed.
   *
   * Null for an Organization Admin, whose authority is not a membership row.
   */
  unitRole: z.string().nullable(),
  /** The unit the Organization Admin placed them in. Null for an Org Admin,
   *  and for a Business Unit Admin appointed before a unit was chosen. */
  businessUnitId: z.string().nullable(),
  businessUnitName: z.string().nullable(),
  bindings: z.array(DirectoryBinding),
  /** In a unit, holding only the `contributor` placeholder — the unit admin
   *  owes them a role. Drives the pending queue. */
  awaitingRole: z.boolean(),
});
export type DirectoryEntry = z.infer<typeof DirectoryEntry>;

export const DirectoryList = z.array(DirectoryEntry);

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
