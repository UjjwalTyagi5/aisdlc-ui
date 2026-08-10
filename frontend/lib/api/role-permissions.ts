import { z } from "zod";

import { api } from "./client";

export const RolePermissionRow = z.object({
  role: z.string(),
  defaults: z.array(z.string()),
  effective: z.array(z.string()),
  overridden: z.boolean(),
  editable: z.boolean(),
});
export type RolePermissionRow = z.infer<typeof RolePermissionRow>;

/** Every built-in role with what it holds, what it shipped with, and whether
 *  an admin has changed it. */
export const listRolePermissions = () =>
  api("/admin/role-permissions", { schema: z.array(RolePermissionRow) });

/** Change a built-in role, or put it back. Organization Admin only. */
export const saveRolePermissions = (input: {
  role: string;
  permissions?: string[];
  reset?: boolean;
}) =>
  api("/admin/role-permissions", {
    method: "PUT",
    body: input,
    schema: RolePermissionRow,
  });
