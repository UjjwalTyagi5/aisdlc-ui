/**
 * Browser-side platform-tier mutations (Phase 4). Goes through the Next route
 * handler proxy (`/api/platform/*`) like the rest of the browser `api()` calls.
 */
import { z } from "zod";

import { api } from "./client";

export interface CreateOrgInput {
  displayName: string;
  slug: string;
  adminEmail: string;
  adminPassword: string;
}

const CreateOrgResponse = z.object({
  org_id: z.string(),
  slug: z.string(),
  display_name: z.string(),
  workspace_id: z.string(),
  admin_email: z.string(),
  admin_user_id: z.string(),
});

export type CreateOrgResult = z.infer<typeof CreateOrgResponse>;

export function createOrganization(input: CreateOrgInput) {
  return api("/platform/organizations", {
    method: "POST",
    body: {
      display_name: input.displayName,
      slug: input.slug,
      admin_email: input.adminEmail,
      admin_password: input.adminPassword,
    },
    schema: CreateOrgResponse,
  });
}

export function setOrgSuspended(orgId: string, suspended: boolean) {
  return api(`/platform/organizations/${orgId}`, {
    method: "PATCH",
    body: { suspended },
    schema: z.unknown(),
  });
}

export function resetOrgAdminPassword(orgId: string, email: string, newPassword: string) {
  return api(`/platform/organizations/${orgId}/reset-admin-password`, {
    method: "POST",
    body: { email, new_password: newPassword },
    schema: z.unknown(),
  });
}

const PlatformUserResult = z.object({
  user_id: z.string(),
  email: z.string(),
  platform_role: z.string(),
  active: z.boolean(),
});

export function createPlatformUser(email: string, password: string, role: string) {
  return api("/platform/users", {
    method: "POST",
    body: { email, password, platform_role: role },
    schema: PlatformUserResult,
  });
}

export function setPlatformUserActive(userId: string, active: boolean) {
  return api(`/platform/users/${userId}`, {
    method: "PATCH",
    body: { active },
    schema: z.unknown(),
  });
}
