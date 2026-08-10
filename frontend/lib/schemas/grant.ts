import { z } from "zod";

/**
 * How far an Organization Admin's approval of a resource reaches.
 *
 *  - `global`   — every Business Unit and every project inside them gets it
 *                 automatically. Nothing has to be granted, requested, or
 *                 re-configured further down.
 *  - `specific` — only the Business Units named on the grant get it. Everyone
 *                 else is as if the resource did not exist: it is absent from
 *                 their catalogue, not merely disabled.
 *
 * Shared verbatim by models (`OrgModelGrant`) and connectors
 * (`ConnectorGrant`) so the two cascades cannot drift into meaning different
 * things by the same word.
 */
export const GrantVisibility = z.enum(["global", "specific"]);
export type GrantVisibility = z.infer<typeof GrantVisibility>;

/** True when a grant reaches the given Business Unit. */
export function grantReaches(
  grant: { visibility: GrantVisibility; businessUnitIds: string[] },
  workspaceId: string | null | undefined,
): boolean {
  if (grant.visibility === "global") return true;
  if (!workspaceId) return false;
  return grant.businessUnitIds.includes(String(workspaceId));
}
