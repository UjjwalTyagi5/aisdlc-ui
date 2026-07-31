import { z } from "zod";

/**
 * Access-management schemas — mirror the backend RBAC vocabulary
 * (platform/backend/shared/authz/permissions.py). Roles are GLOBAL; role
 * assignments (UserWorkspaceRole) are tenant-scoped under RLS.
 */

export const AccessRole = z.object({
  name: z.string(),
  label: z.string(),
  description: z.string(),
  permissions: z.array(z.string()),
});
export type AccessRole = z.infer<typeof AccessRole>;

export const AccessWorkspace = z.object({
  id: z.string(),
  name: z.string(),
});
export type AccessWorkspace = z.infer<typeof AccessWorkspace>;

export const AccessMember = z.object({
  userId: z.string(),
  name: z.string().nullable(),
  email: z.string().nullable(),
  initials: z.string(),
  roles: z.array(z.string()),
});
export type AccessMember = z.infer<typeof AccessMember>;

export const AccessRoleList = z.array(AccessRole);
export const AccessWorkspaceList = z.array(AccessWorkspace);
export const AccessMemberList = z.array(AccessMember);

/** All users in the org (no workspace context — just identity). */
export const OrgMember = z.object({
  userId: z.string(),
  email: z.string().nullable(),
  initials: z.string(),
});
export type OrgMember = z.infer<typeof OrgMember>;
export const OrgMemberList = z.array(OrgMember);
