/**
 * Project-scoped member API client. Calls the same-origin BFF
 * (`/api/projects/:id/members*`) — a net-new project-role model, distinct
 * from `lib/api/workspaces.ts`'s Business-Unit-scoped members.
 */
import { z } from "zod";

import { ProjectMember } from "@/lib/schemas/project-membership";
import type { ProjectId } from "@/lib/schemas";

import { api } from "./client";

export const listProjectMembers = (projectId: ProjectId) =>
  api(`/projects/${encodeURIComponent(projectId)}/members`, { schema: z.array(ProjectMember) });

export const addProjectMember = (
  projectId: ProjectId,
  input: { email: string; displayName?: string; roleName: string },
) =>
  api(`/projects/${encodeURIComponent(projectId)}/members`, {
    method: "POST",
    body: input,
    schema: ProjectMember,
  });

export const updateProjectMemberRole = (
  projectId: ProjectId,
  membershipId: string,
  roleName: string,
) =>
  api(`/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(membershipId)}`, {
    method: "PATCH",
    body: { roleName },
    schema: ProjectMember,
  });

export const removeProjectMember = (projectId: ProjectId, membershipId: string) =>
  api(`/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(membershipId)}`, {
    method: "DELETE",
  });

/**
 * Ask another Business Unit to lend a contributor to this project.
 *
 * By EMAIL, because the asker cannot see the other unit's people — see
 * `lib/mock/cross-bu.ts`. Returns the raised request; the seat appears only
 * once the contributor's own admin approves it.
 */
export const requestCrossBuMember = (
  projectId: ProjectId,
  input: { email: string; roleName: string; reason?: string },
) =>
  api(`/projects/${encodeURIComponent(projectId)}/access-requests`, {
    method: "POST",
    body: input,
  });
