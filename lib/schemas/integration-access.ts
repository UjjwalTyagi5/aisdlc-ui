import { z } from "zod";

/**
 * Runtime-validated shapes for the Integrations access matrix. Mirrors
 * `lib/mock/integration-access.ts`, which stays zod-free — keep the two in
 * sync.
 */

export const AccessProjectEntry = z.object({
  id: z.string(),
  name: z.string(),
  /** Stages the project wired it to. Present for connectors; MCP servers are
   *  assigned per stage too, so both can carry it. */
  stages: z.array(z.string()).default([]),
});
export type AccessProjectEntry = z.infer<typeof AccessProjectEntry>;

export const AccessUnitEntry = z.object({
  id: z.string(),
  name: z.string(),
  /** `granted` — the Org Admin gave it to this unit. `none` — the unit does
   *  not have it, and is listed so it can be given it. */
  via: z.enum(["granted", "none"]),
  projects: z.array(AccessProjectEntry).default([]),
});
export type AccessUnitEntry = z.infer<typeof AccessUnitEntry>;

export const IntegrationAccessRow = z.object({
  kind: z.enum(["connector", "mcp"]),
  /** A connector kind (`jira`) or an MCP server id (`mcp_postgres`). */
  id: z.string(),
  name: z.string(),
  description: z.string().nullable(),
  origin: z.enum(["organization", "business_unit"]),
  /** Whether anything is actually connected behind it. A granted kind with no
   *  connection is a permission with nothing to use yet. */
  onboarded: z.boolean(),
  /** Every unit the viewer may see, granted or not — `via` says which. */
  units: z.array(AccessUnitEntry).default([]),
  /** How many of them actually hold it. `units.length` counts candidates. */
  grantedUnitCount: z.number().int().nonnegative(),
  projectCount: z.number().int().nonnegative(),
});
export type IntegrationAccessRow = z.infer<typeof IntegrationAccessRow>;
