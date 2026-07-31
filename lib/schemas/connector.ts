import { z } from "zod";

import { ConnectorId, TenantId } from "./ids";
import { ConnectorHealth, ConnectorKind } from "./enums";
import { Timestamp } from "./primitives";

/** Declares what a connector can read/write. Rendered in the scope inspector. */
export const Capability = z.object({
  key: z.string(),
  description: z.string(),
  mode: z.enum(["read", "write", "event"]),
});
export type Capability = z.infer<typeof Capability>;

/**
 * The level a connector was onboarded at (PRD §34.3).
 *
 * Connectors cascade the same way models do: an Org Admin onboards
 * `organization`-scoped ones available everywhere, a Business Unit Admin
 * onboards `business_unit`-scoped ones available only inside their unit. A
 * project inherits the union of the two and its Project Admin chooses which
 * of them the project actually uses; contributors then use that selection.
 *
 * Before this existed, every connector was flat and tenant-wide: each
 * Business Unit saw every other unit's integrations, and there was no level
 * at which "onboard" and "select" were different acts.
 */
export const ConnectorScope = z.enum(["organization", "business_unit"]);
export type ConnectorScope = z.infer<typeof ConnectorScope>;

export const Connector = z.object({
  id: ConnectorId,
  tenantId: TenantId,
  kind: ConnectorKind,
  name: z.string(),
  installed: z.boolean(),
  health: ConnectorHealth,
  capabilities: z.array(Capability),
  lastCheckedAt: Timestamp.nullable(),
  /** URL or account identifier the token authorizes — e.g. org/name or workspace.
   *  nullish: FastAPI emits `null` when the health-cache entry has no org_url/account. */
  account: z.string().nullish(),
  /** Defaulted so a backend that predates the cascade still parses — an
   *  un-scoped connector reads as org-wide, which is what flat meant. */
  scope: ConnectorScope.default("organization"),
  /** The owning Business Unit for a `business_unit`-scoped connector; null for org-wide. */
  workspaceId: z.string().nullable().default(null),
});
export type Connector = z.infer<typeof Connector>;

/**
 * Wave C — install response union (REQ-M7-22).
 *
 * FastAPI returns `{redirect_url: string}` for OAuth-based connectors (Jira,
 * GitHub, Slack, Azure Repos). The BFF normalises the key to `redirectUrl`.
 * For non-OAuth connectors the BFF may still return a full Connector record
 * directly (backward-compat path).
 */
export const ConnectorInstallResponse = z.union([
  Connector,
  z.object({ redirectUrl: z.string() }),
]);
export type ConnectorInstallResponse = z.infer<typeof ConnectorInstallResponse>;

/**
 * Result of POST /connectors/{kind}/credentials — direct credential entry
 * (Azure DevOps PAT, Jira email + API token). The backend stores the secret
 * and runs a live verify probe; `status` reflects whether the probe succeeded.
 * The secret itself is never returned.
 */
export const SetCredentialsResult = z.object({
  kind: z.string(),
  status: z.enum(["valid", "invalid"]),
  account: z.string().nullish(),
  error: z.string().nullish(),
});
export type SetCredentialsResult = z.infer<typeof SetCredentialsResult>;
