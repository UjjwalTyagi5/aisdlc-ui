/**
 * People-directory API client — the Users & Roles table and its click-through
 * detail view. Calls the same-origin BFF (`/api/admin/users`).
 */
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
