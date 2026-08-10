/**
 * Access-management API client. Calls the BFF (/api/admin/*), which proxies to
 * FastAPI with the signed bearer. In mock mode MSW intercepts these paths.
 */
import { api } from "./client";
import {
  AccessMemberList,
  AccessRoleList,
  AccessWorkspaceList,
  OrgMemberList,
} from "@/lib/schemas/access";

export function listAccessWorkspaces() {
  return api("/admin/workspaces", { schema: AccessWorkspaceList });
}

export function listAccessRoles() {
  return api("/admin/roles", { schema: AccessRoleList });
}

export function listAccessMembers(workspaceId: string) {
  return api("/admin/members", {
    query: { workspace_id: workspaceId },
    schema: AccessMemberList,
  });
}

export interface AccessAssignmentInput {
  userId: string;
  workspaceId: string;
  roleName: string;
}

export function assignAccessRole(input: AccessAssignmentInput) {
  return api("/admin/assignments", {
    method: "POST",
    body: {
      user_id: input.userId,
      workspace_id: input.workspaceId,
      role_name: input.roleName,
    },
  });
}

export function revokeAccessRole(input: AccessAssignmentInput) {
  return api("/admin/assignments", {
    method: "DELETE",
    body: {
      user_id: input.userId,
      workspace_id: input.workspaceId,
      role_name: input.roleName,
    },
  });
}

/** All active users in the org — no workspace context (full roster). */
export function listOrgMembers() {
  return api("/admin/org-members", { schema: OrgMemberList });
}
