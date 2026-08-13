/**
 * Connector visibility across the org → Business Unit → project cascade
 * (PRD §34.3). Plain functions over a connector list, server-safe and shared
 * by `app/api/connectors/route.ts` and `mocks/handlers.ts` so the two runtimes
 * can never disagree about who sees what — see [[msw-dual-runtime-mutation-rule]].
 *
 * There is deliberately no store here: which *level* a connector was onboarded
 * at is a property of the connector (`scope`/`workspaceId`), and which of the
 * inherited ones a project uses is a property of the project
 * (`project.connectors`). Nothing needs a third table.
 */
import { filterConnectorsByGrant } from "@/lib/mock/connector-grants";
import type { Connector } from "@/lib/schemas/connector";

/**
 * What a Business Unit can see: every org-wide connector whose kind the
 * Organization Admin permitted this unit, plus the ones onboarded into the
 * unit itself. A null `workspaceId` means the caller has no unit context and
 * gets the whole tenant list — the onboarding wizard and the dashboard health
 * tile both legitimately want that.
 *
 * Inheritance is one-way by construction: a unit never sees a sibling unit's
 * connectors. It is no longer unconditional, though — an org-wide connector
 * whose kind is granted only to *other* units is hidden here, which is the
 * whole point of a `specific` grant (lib/mock/connector-grants.ts).
 */
export function visibleConnectors(
  connectors: Connector[],
  workspaceId: string | null | undefined,
): Connector[] {
  if (!workspaceId) return connectors;
  return filterConnectorsByGrant(
    connectors.filter((c) => c.scope === "organization" || c.workspaceId === workspaceId),
    workspaceId,
  );
}

/**
 * The same question, asked safely for a viewer whose reach is bounded.
 *
 * `visibleConnectors`'s "no workspaceId means the whole tenant" default is
 * correct for a caller with no unit context, but it is a leak for a caller with
 * a bounded one: the dashboard health tile and the onboarding wizard both call
 * with no id, and a Business Unit Admin would get every sibling unit's
 * integrations. This wrapper closes that by intersecting with what the viewer
 * may read:
 *
 *  - `allowedWorkspaceIds: null` — unbounded (Organization Admin). Unchanged
 *    behaviour, including the whole-tenant default.
 *  - a requested `workspaceId` outside the allowed set returns EMPTY rather
 *    than falling back to the tenant list. Silently widening a denied narrow
 *    request into a broader answer is how these leaks get written.
 */
export function visibleConnectorsForScope(
  connectors: Connector[],
  workspaceId: string | null | undefined,
  allowedWorkspaceIds: string[] | null,
): Connector[] {
  if (allowedWorkspaceIds === null) return visibleConnectors(connectors, workspaceId);
  if (workspaceId) {
    if (!allowedWorkspaceIds.includes(String(workspaceId))) return [];
    return visibleConnectors(connectors, workspaceId);
  }
  // No unit named, bounded viewer: the union over the units they may read.
  // Deduped by id, since an org-wide connector permitted to two of their units
  // would otherwise appear twice.
  const seen = new Set<string>();
  return allowedWorkspaceIds.flatMap((id) =>
    visibleConnectors(connectors, id).filter((c) => {
      if (seen.has(String(c.id))) return false;
      seen.add(String(c.id));
      return true;
    }),
  );
}

/**
 * The level an admin's newly onboarded connector lands at.
 *
 * Re-exported, not restated. The rule lives in `lib/integrations/manage-scope.ts`
 * beside the manage check it has to agree with; this module keeps the name so the
 * MSW handlers that import it here are unaffected, and so the mock runtime and
 * the app can never answer this differently.
 */
export { onboardingScopeFor } from "@/lib/integrations/manage-scope";

/**
 * Record a pasted credential against a connector kind, creating the connection
 * if this is the first one — the write behind "Add credentials" on the
 * Integrations page.
 *
 * The level it lands at comes from who is doing it (`onboardingScopeFor`), so
 * a Business Unit Admin's key produces a connection inside their unit rather
 * than an org-wide one every sibling inherits. An existing connection keeps
 * the level it was onboarded at: re-keying is not re-scoping, and quietly
 * promoting a unit's connection to org-wide because an Org Admin rotated its
 * token would hand every other unit an integration nobody granted them.
 *
 * Returns the connection, or null when the kind isn't in the catalogue at all.
 */
export function recordConnectorCredentials(
  connectors: Connector[],
  kind: string,
  onboarder: { scope: Connector["scope"]; workspaceId: string | null; tenantId: string },
  account?: string | null,
): Connector | null {
  const now = new Date().toISOString();
  const existing = connectors.find((c) => c.kind === kind);
  if (existing) {
    existing.installed = true;
    existing.health = "healthy";
    existing.lastCheckedAt = now;
    if (account) existing.account = account;
    return existing;
  }

  const created = {
    id: `conn_${kind}`,
    tenantId: onboarder.tenantId,
    kind,
    name: kind,
    installed: true,
    health: "healthy",
    capabilities: [],
    lastCheckedAt: now,
    account: account ?? null,
    scope: onboarder.scope,
    workspaceId: onboarder.scope === "business_unit" ? onboarder.workspaceId : null,
  } as unknown as Connector;
  connectors.push(created);
  return created;
}
