/**
 * Server-only BFF calls for the platform (Application Team) tier.
 * Mirrors lib/api/auth.ts: uses the server-only `bffFetch` transport (mints the
 * backend JWT from the session) — never the browser `api()` client.
 */
import { z } from "zod";

import type { Session } from "@/lib/auth/types";
import { bffFetch } from "@/lib/bff/client";

const OrganizationsResponse = z.array(
  z.object({
    id: z.string(),
    slug: z.string(),
    display_name: z.string(),
    suspended: z.boolean(),
    member_count: z.number(),
    run_count: z.number(),
  }),
);

export type PlatformOrg = z.infer<typeof OrganizationsResponse>[number];

/**
 * Calls FastAPI `GET /platform/organizations` (platform:*-gated, cross-tenant).
 * Throws on non-2xx so the caller can render an error state — unlike the
 * fail-closed permission bootstrap, a platform admin genuinely needs to see
 * whether the listing failed.
 */
export async function fetchOrganizations(session: Session): Promise<PlatformOrg[]> {
  return bffFetch("/platform/organizations", {
    session,
    schema: OrganizationsResponse,
  });
}

const OrgDetailResponse = z.object({
  org_id: z.string(),
  slug: z.string(),
  display_name: z.string(),
  suspended: z.boolean(),
  workspaces: z.array(z.object({ id: z.string(), slug: z.string(), display_name: z.string() })),
  admins: z.array(z.object({ user_id: z.string(), email: z.string() })),
  members: z
    .array(
      z.object({ user_id: z.string(), email: z.string().nullable(), roles: z.array(z.string()) }),
    )
    .optional()
    .default([]),
  member_count: z.number(),
  run_count: z.number(),
  total_cost_usd: z.number(),
});

export type OrgDetail = z.infer<typeof OrgDetailResponse>;

export async function fetchOrgDetail(session: Session, orgId: string): Promise<OrgDetail> {
  return bffFetch(`/platform/organizations/${orgId}`, {
    session,
    schema: OrgDetailResponse,
  });
}

const PlatformUsersResponse = z.array(
  z.object({
    user_id: z.string(),
    email: z.string(),
    platform_role: z.string(),
    active: z.boolean(),
    created_at: z.string().nullable().optional(),
  }),
);

export type PlatformUserRow = z.infer<typeof PlatformUsersResponse>[number];

export async function fetchPlatformUsers(session: Session): Promise<PlatformUserRow[]> {
  return bffFetch("/platform/users", { session, schema: PlatformUsersResponse });
}
