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
import type { Connector, ConnectorGrant } from "@/lib/schemas/connector";
import type { ConnectorKind } from "@/lib/schemas/enums";

/**
 * Seeded so every state the access screen can show carries real data.
 *
 * Crucially `azure_devops` reaches only two of the three units. With every
 * org-wide kind granted to everyone there was NO ungranted unit anywhere, so
 * the "grant this unit" control had nothing to render and the screen looked
 * like it could only ever revoke ([[scoped-fixtures-need-coverage]]).
 */
const ALL_UNITS = ["ws_lending", "ws_payments", "ws_platform"];

let GRANTS: ConnectorGrant[] = [
  { kind: "jira", businessUnitIds: [...ALL_UNITS] },
  { kind: "github", businessUnitIds: [...ALL_UNITS] },
  { kind: "azure_devops", businessUnitIds: ["ws_lending", "ws_payments"] },
  { kind: "slack", businessUnitIds: ["ws_payments"] },
  { kind: "github_actions", businessUnitIds: ["ws_payments", "ws_lending"] },
  { kind: "ms_teams", businessUnitIds: ["ws_platform"] },
  { kind: "sharepoint", businessUnitIds: ["ws_lending"] },
];

export function listConnectorGrants(): ConnectorGrant[] {
  return GRANTS.map((g) => ({ ...g, businessUnitIds: [...g.businessUnitIds] }));
}

/** Replace the whole list. A kind granted to no unit is dropped rather than
 *  stored empty: "granted to nobody" and "not granted" are the same state, and
 *  keeping both invites a UI that distinguishes them. */
export function setConnectorGrants(grants: ConnectorGrant[]): ConnectorGrant[] {
  const seen = new Set<string>();
  GRANTS = grants.flatMap((g) => {
    if (seen.has(g.kind)) return [];
    seen.add(g.kind);
    const units = [...new Set(g.businessUnitIds)];
    return units.length > 0 ? [{ kind: g.kind, businessUnitIds: units }] : [];
  });
  return listConnectorGrants();
}

/** The grants that reach one Business Unit — kept as grants rather than bare
 *  kinds so the UI can say WHY a kind is there (inherited globally vs granted
 *  to this unit), which is the difference between "everyone has this" and
 *  "someone chose to give you this". */
export function connectorGrantsForWorkspace(workspaceId: string): ConnectorGrant[] {
  return listConnectorGrants().filter((g) => g.businessUnitIds.includes(String(workspaceId)));
}

/**
 * The union of grants reaching ANY of a viewer's units — what someone bound to
 * two units may connect, without asking them which unit they meant.
 *
 * `businessUnitIds` is narrowed to the viewer's OWN units rather than emptied.
 * Emptying it now would read as "granted to nobody", which is exactly what an
 * absent grant means since visibility went away. A viewer bounded to Lending
 * still has no business learning that a grant also names Payments, so the
 * sibling ids are filtered out — the org-wide `listConnectorGrants()` keeps
 * them all, because the Org Admin is the one who wrote them.
 */
export function connectorGrantsForWorkspaces(workspaceIds: string[]): ConnectorGrant[] {
  const own = new Set(workspaceIds.map(String));
  const seen = new Set<string>();
  return workspaceIds.flatMap((id) =>
    connectorGrantsForWorkspace(id).flatMap((g) => {
      if (seen.has(g.kind)) return [];
      seen.add(g.kind);
      return [{ ...g, businessUnitIds: g.businessUnitIds.filter((x) => own.has(String(x))) }];
    }),
  );
}

/** Just the kinds, for filtering. */
export function permittedConnectorKinds(workspaceId: string): ConnectorKind[] {
  return connectorGrantsForWorkspace(workspaceId).map((g) => g.kind);
}

/**
 * Grant a Business Unit exactly `kinds` — the Org Admin's per-unit control,
 * used when creating a unit and from the Integrations access matrix.
 *
 * Every kind is now touchable, where before only `specific` grants were: with
 * one explicit list per kind there is no "global" whose revocation would have
 * meant demoting it for the whole organization. Removing a unit here removes
 * exactly that unit.
 *
 * A kind absent from the catalogue is CREATED rather than ignored — granting
 * the first unit is how a kind enters the estate now that nothing is implicitly
 * available to everyone.
 */
export function setBuConnectorGrants(workspaceId: string, kinds: string[]): ConnectorGrant[] {
  const wanted = new Set(kinds);
  for (const grant of GRANTS) {
    const has = grant.businessUnitIds.includes(workspaceId);
    const want = wanted.has(grant.kind);
    if (want && !has) grant.businessUnitIds.push(workspaceId);
    else if (!want && has) {
      grant.businessUnitIds = grant.businessUnitIds.filter((id) => id !== workspaceId);
    }
  }
  for (const kind of wanted) {
    if (!GRANTS.some((g) => g.kind === kind)) {
      GRANTS.push({ kind: kind as ConnectorKind, businessUnitIds: [workspaceId] });
    }
  }
  // A kind left reaching nobody is not granted at all — see setConnectorGrants.
  GRANTS = GRANTS.filter((g) => g.businessUnitIds.length > 0);
  return connectorGrantsForWorkspace(workspaceId);
}

/**
 * Give ONE unit access to one kind — the inverse of `revokeConnectorGrant`,
 * and the only way a unit gains an integration after it was created.
 *
 * Creates the grant when the kind has none, so the first unit to be given
 * something is also what puts it in the estate.
 */
export function grantConnectorToUnit(kind: string, workspaceId: string): string[] {
  let grant = GRANTS.find((g) => g.kind === kind);
  if (!grant) {
    grant = { kind: kind as ConnectorKind, businessUnitIds: [] };
    GRANTS.push(grant);
  }
  if (!grant.businessUnitIds.includes(String(workspaceId))) {
    grant.businessUnitIds.push(String(workspaceId));
  }
  return [...grant.businessUnitIds];
}

/**
 * Revoke ONE unit's access to one kind — the Org Admin's action on the access
 * matrix, where the row is a kind and the cell is a unit.
 *
 * Returns the surviving units so the caller can render the consequence without
 * a refetch: revoking the last one removes the kind from the estate entirely,
 * and that is worth saying out loud rather than watching a row vanish.
 */
export function revokeConnectorGrant(kind: string, workspaceId: string): string[] {
  const grant = GRANTS.find((g) => g.kind === kind);
  if (!grant) return [];
  grant.businessUnitIds = grant.businessUnitIds.filter((id) => id !== String(workspaceId));
  const left = [...grant.businessUnitIds];
  GRANTS = GRANTS.filter((g) => g.businessUnitIds.length > 0);
  return left;
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
