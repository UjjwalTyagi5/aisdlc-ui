/**
 * People-directory API client — the Users & Roles table and its click-through
 * detail view. Calls the same-origin BFF (`/api/admin/users`).
 */
import { z } from "zod";

import { DirectoryList, UserDetail } from "@/lib/schemas/user-directory";

import { api } from "./client";

/** Every person in the organisation, with their org-level appointment and
 *  every role they hold. Org-wide by design — see the route handler. */
export const listUserDirectory = () => api("/admin/users", { schema: DirectoryList });

export const getUserDetail = (id: string) =>
  api(`/admin/users/${encodeURIComponent(id)}`, { schema: UserDetail });

/** Change an org-level appointment — Business Unit Admin or Contributor, and
 *  which unit. Organization Admin only; the route enforces it. */
export const changeOrgAppointment = (
  id: string,
  body: { role: string; workspaceId: string | null },
) => api(`/admin/users/${encodeURIComponent(id)}`, { method: "PATCH", body });

/** A live cross-unit loan, as one of the two admins involved sees it. */
export const CrossBuGrantOut = z.object({
  id: z.string(),
  identityId: z.string(),
  displayName: z.string(),
  projectId: z.string(),
  projectName: z.string(),
  parentWorkspaceId: z.string(),
  parentWorkspaceName: z.string(),
  targetWorkspaceId: z.string(),
  targetWorkspaceName: z.string(),
  role: z.string(),
  approvedBy: z.string(),
  approvedAt: z.string(),
  /** True when the viewer's unit is the one that lent them out. */
  lentByYou: z.boolean(),
});
export type CrossBuGrantOut = z.infer<typeof CrossBuGrantOut>;

/** Loans touching a unit the viewer administers — lent out and borrowed in. */
export const listCrossBuGrants = () =>
  api("/admin/cross-bu-grants", { schema: z.array(CrossBuGrantOut) });

/** End a loan. Only the lending unit's admin may; the route enforces it. */
export const revokeCrossBuGrant = (body: { identityId: string; projectId: string }) =>
  api("/admin/cross-bu-grants", { method: "DELETE", body });
