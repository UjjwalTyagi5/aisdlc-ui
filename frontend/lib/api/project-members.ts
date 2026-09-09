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

/** Change the agents this member reaches BEYOND their role's own.
 *
 * `role_bindings.extra_agents` is a stored column and the PATCH has always accepted it,
 * but nothing sent it: the only caller posted `{ roleName }`. So an extra agent granted
 * at project creation could never be taken away or added to afterwards — a one-sided
 * door, and the wrong side to be stuck on for a grant.
 *
 * DISTINCT FROM THE ROLE'S OWN AGENTS, which are derived from the binding at read time
 * and are not stored anywhere. This is the exception list, and only it is editable.
 * Sending `[]` clears the extras and leaves the role's defaults untouched.
 */
export const updateProjectMemberAgents = (
  projectId: ProjectId,
  membershipId: string,
  extraAgents: string[],
) =>
  api(`/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(membershipId)}`, {
    method: "PATCH",
    body: { extraAgents },
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
