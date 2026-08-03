/**
 * Which connector kinds the Organization Admin permits, and how far each
 * permission reaches — the connector half of the org grant model, mirroring
 * `ORG_GRANTS` in lib/mock/model-fixtures.ts.
 *
 * Plain data + pure functions, server-safe, shared by the Next route handlers
 * (app/api/connectors/**) and mocks/handlers.ts so the two runtimes can never
 * disagree about who may connect what — see [[msw-dual-runtime-mutation-rule]].
 *
 * This answers a different question from `Connector.scope`, which records
 * where an existing connection was onboarded. This one comes first: whether a
 * kind may be onboarded or used inside a given Business Unit at all. Without
 * a grant, the kind is absent from that unit's catalogue and any org-wide
 * connection of that kind stops being inherited by it.
 */
import { grantReaches } from "@/lib/schemas/grant";
import type { Connector, ConnectorGrant } from "@/lib/schemas/connector";
import type { ConnectorKind } from "@/lib/schemas/enums";

/**
 * Seeded so both visibilities carry real data: the three integrations every
 * unit needs are global, while the two that tend to be team-specific are
 * granted to named units only. With everything global the per-unit grant UI
 * would never render a selection and would read as broken.
 */
let GRANTS: ConnectorGrant[] = [
  { kind: "jira", visibility: "global", businessUnitIds: [] },
  { kind: "github", visibility: "global", businessUnitIds: [] },
  { kind: "azure_devops", visibility: "global", businessUnitIds: [] },
  { kind: "slack", visibility: "specific", businessUnitIds: ["ws_payments"] },
  { kind: "github_actions", visibility: "specific", businessUnitIds: ["ws_payments", "ws_lending"] },
];

export function listConnectorGrants(): ConnectorGrant[] {
  return GRANTS.map((g) => ({ ...g, businessUnitIds: [...g.businessUnitIds] }));
}

/** Replace the whole list. A `global` grant's unit list is cleared, since it
 *  reaches every unit regardless and a stale list only misleads. */
export function setConnectorGrants(grants: ConnectorGrant[]): ConnectorGrant[] {
  const seen = new Set<string>();
  GRANTS = grants.flatMap((g) => {
    if (seen.has(g.kind)) return [];
    seen.add(g.kind);
    return [
      {
        kind: g.kind,
        visibility: g.visibility,
        businessUnitIds: g.visibility === "specific" ? [...new Set(g.businessUnitIds)] : [],
      },
    ];
  });
  return listConnectorGrants();
}

/** The grants that reach one Business Unit — kept as grants rather than bare
 *  kinds so the UI can say WHY a kind is there (inherited globally vs granted
 *  to this unit), which is the difference between "everyone has this" and
 *  "someone chose to give you this". */
export function connectorGrantsForWorkspace(workspaceId: string): ConnectorGrant[] {
  return listConnectorGrants().filter((g) => grantReaches(g, workspaceId));
}

/**
 * The union of grants reaching ANY of a viewer's units — what someone bound to
 * two units may connect, without asking them which unit they meant.
 *
 * `businessUnitIds` is deliberately emptied. A viewer bounded to Lending has no
 * business learning that a grant also names Payments; the only thing they need
 * from the record is that the kind is permitted and whether it arrived globally
 * or by a specific grant. The org-wide `listConnectorGrants()` keeps the ids,
 * because the Org Admin is the one who wrote them.
 */
export function connectorGrantsForWorkspaces(workspaceIds: string[]): ConnectorGrant[] {
  const seen = new Set<string>();
  return workspaceIds.flatMap((id) =>
    connectorGrantsForWorkspace(id).flatMap((g) => {
      if (seen.has(g.kind)) return [];
      seen.add(g.kind);
      return [{ ...g, businessUnitIds: [] }];
    }),
  );
}

/** Just the kinds, for filtering. */
export function permittedConnectorKinds(workspaceId: string): ConnectorKind[] {
  return connectorGrantsForWorkspace(workspaceId).map((g) => g.kind);
}

/**
 * Grant a Business Unit exactly `kinds` — the Org Admin's per-unit control,
 * used when creating a unit and from its management page.
 *
 * Only `specific` grants are touched, for the same reason
 * `setBuModelGrants` skips global models: a globally granted kind reaches
 * every unit by definition, so revoking it *here* could only mean demoting it
 * for the whole organization, which is not what "manage this unit" means.
 * A kind with no grant at all is ignored — a unit cannot be given something
 * the organization has not permitted.
 */
export function setBuConnectorGrants(workspaceId: string, kinds: string[]): ConnectorGrant[] {
  const wanted = new Set(kinds);
  for (const grant of GRANTS) {
    if (grant.visibility !== "specific") continue;
    const has = grant.businessUnitIds.includes(workspaceId);
    const want = wanted.has(grant.kind);
    if (want && !has) grant.businessUnitIds.push(workspaceId);
    else if (!want && has) {
      grant.businessUnitIds = grant.businessUnitIds.filter((id) => id !== workspaceId);
    }
  }
  return connectorGrantsForWorkspace(workspaceId);
}

/**
 * Drop connections whose kind the given Business Unit was never permitted.
 *
 * Applied on top of `visibleConnectors`' scope filter, not instead of it: the
 * two answer different questions (was this onboarded somewhere I can see, and
 * am I allowed this kind at all) and a connection has to pass both. Called
 * with no unit context — an org-wide viewer — nothing is dropped, because the
 * grants are exactly that viewer's own decisions.
 */
export function filterConnectorsByGrant(
  connectors: Connector[],
  workspaceId: string | null | undefined,
): Connector[] {
  if (!workspaceId) return connectors;
  const permitted = new Set<string>(permittedConnectorKinds(workspaceId));
  return connectors.filter((c) => permitted.has(c.kind));
}
