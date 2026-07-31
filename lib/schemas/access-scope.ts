import { z } from "zod";

/**
 * The viewer's resolved access scope — WHICH Business Units and projects they
 * may see, as opposed to WHAT actions they may take (that is `permissions`).
 *
 * Served by `GET /api/auth/access-scope` so client components read one
 * authoritative answer instead of each re-deriving the boundary from fixtures.
 * Mirrors `lib/mock/access-scope.ts::AccessScope`; when a real backend resolves
 * scope on the session, the endpoint's body changes and this schema does not.
 */

export const ScopeKind = z.enum(["organization", "business_unit", "project"]);
export type ScopeKind = z.infer<typeof ScopeKind>;

export const ScopeBinding = z.object({
  kind: ScopeKind,
  scopeId: z.string(),
  scopeName: z.string(),
  role: z.string(),
  parentId: z.string().nullable(),
  parentName: z.string().nullable(),
  status: z.enum(["active", "invited", "deactivated"]),
});
export type ScopeBinding = z.infer<typeof ScopeBinding>;

export const AccessScopeOut = z.object({
  level: ScopeKind,
  isOrgWide: z.boolean(),
  businessUnitIds: z.array(z.string()),
  managedBusinessUnitIds: z.array(z.string()),
  projectIds: z.array(z.string()),
  managedProjectIds: z.array(z.string()),
  actingBindings: z.array(ScopeBinding),
  allBindings: z.array(ScopeBinding),
  identityId: z.string().nullable(),
});
export type AccessScopeOut = z.infer<typeof AccessScopeOut>;
