import { z } from "zod";

/**
 * Runtime-validated shapes for the Integrations access matrix. Mirrors
 * `lib/mock/integration-access.ts`, which stays zod-free — keep the two in
 * sync.
 */

/**
 * Read, write, or both. Mirrors `ACCESS_LEVELS` in
 * `backend/shared/authz/connector_access.py` — the server is authoritative and
 * this is only the shape the UI validates against.
 *
 * `read` and `write` are INCOMPARABLE: neither contains the other, so anything
 * comparing them must use the subset helpers rather than ordering them. Ranking
 * the three would quietly make `write` imply `read`.
 */
export const ConnectorAccessLevel = z.enum(["read", "write", "read_write"]);
export type ConnectorAccessLevel = z.infer<typeof ConnectorAccessLevel>;

/** Human phrasing, matching `connector_access.label()` server-side. */
export const ACCESS_LEVEL_LABEL: Record<ConnectorAccessLevel, string> = {
  read: "Read only",
  write: "Write only",
  read_write: "Read and write",
};

/** What each level lets an agent do, for the picker's helper text. */
export const ACCESS_LEVEL_HINT: Record<ConnectorAccessLevel, string> = {
  read: "Agents can pull data from it, and cannot change anything.",
  write: "Agents can send data to it, and cannot read from it.",
  read_write: "Agents can both read from it and change it.",
};

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
  /**
   * Read, write, or both — null for a unit holding nothing. There is no level
   * without a grant, and defaulting one here would show a value the database
   * does not have. Optional so an older backend still parses.
   */
  access: ConnectorAccessLevel.nullish().default(null),
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
  /**
   * The widest level this connector can actually honour — the grant control's
   * ceiling, so an admin is never offered a level the server will refuse.
   *
   * null means "no ceiling known": an MCP server, or a connector that could not be
   * introspected. The UI must not treat that as "nothing allowed", mirroring the
   * refuse-only-on-positive-knowledge rule the server-side check follows.
   */
  supportedAccess: ConnectorAccessLevel.nullish().default(null),
  /** Every unit the viewer may see, granted or not — `via` says which. */
  units: z.array(AccessUnitEntry).default([]),
  /** How many of them actually hold it. `units.length` counts candidates. */
  grantedUnitCount: z.number().int().nonnegative(),
  projectCount: z.number().int().nonnegative(),
});
export type IntegrationAccessRow = z.infer<typeof IntegrationAccessRow>;


/**
 * One integration's access for a single project: what the unit holds, whether the
 * project narrowed it, and what that leaves.
 *
 * All three are reported together on purpose — "read, and your unit has read and
 * write" is a different situation from "read, and that is all there is". The first
 * is a narrowing somebody chose and can undo here; the second needs a decision a
 * rung up, from an Organization Admin.
 */
export const ProjectIntegrationAccess = z.object({
  kind: z.enum(["connector", "mcp"]),
  targetId: z.string(),
  unitAccess: ConnectorAccessLevel,
  /** null means the project is not narrowed and inherits the unit's level. */
  projectAccess: ConnectorAccessLevel.nullable(),
  effectiveAccess: ConnectorAccessLevel.nullable(),
  effectiveLabel: z.string(),
  inherited: z.boolean(),
});
export type ProjectIntegrationAccess = z.infer<typeof ProjectIntegrationAccess>;
