import { z } from "zod";

/**
 * The Organization Admin's dashboard rollup — one request behind the whole
 * org-variant dashboard.
 *
 * Deliberately a single endpoint rather than four counts assembled in the
 * browser. The figures it returns (how many providers, connectors and people
 * the organization has) are each derivable client-side only by fanning out over
 * per-scope list endpoints and de-duplicating — which is what the Users page
 * already does, and why its total double-counts anyone in two units. Counting
 * server-side, where the whole set is in hand, is both correct and one round
 * trip.
 *
 * Every figure is scoped to what the caller may read; a non-org-wide viewer
 * gets their own slice rather than a 403, so the same shape can back a Business
 * Unit Admin's dashboard later without a second contract.
 */

export const OrgBudgetRow = z.object({
  workspaceId: z.string(),
  name: z.string(),
  /** Null = no cap set for this unit (inherits / unlimited). */
  monthlyBudgetUsd: z.number().nullable(),
  monthlySpendUsd: z.number(),
  isActive: z.boolean(),
});
export type OrgBudgetRow = z.infer<typeof OrgBudgetRow>;

export const OrgOverview = z.object({
  /** Distinct people, not memberships — see listIdentities(). */
  userCount: z.number().int().nonnegative(),
  /** Provider connections across both tiers — org-wide and unit-scoped. */
  modelProviderCount: z.number().int().nonnegative(),
  /** Connector integrations that are installed and not disconnected. */
  connectorCount: z.number().int().nonnegative(),
  /** Every connector on record, installed or not — the denominator. */
  connectorTotalCount: z.number().int().nonnegative(),
  businessUnitCount: z.number().int().nonnegative(),
  projectCount: z.number().int().nonnegative(),
  budgets: z.array(OrgBudgetRow),
  generatedAt: z.string(),
});
export type OrgOverview = z.infer<typeof OrgOverview>;
