/**
 * Which integrations an admin may CHANGE, as opposed to see.
 *
 * The two questions came apart when the admin tiers stopped consuming
 * integrations. Before, an admin who could see a connection could edit it,
 * because seeing it meant it was theirs. Now an Organization Admin sees the
 * whole estate — that is the point of the reach view — while owning only the
 * org-wide part of it, and a Business Unit Admin owns only their unit's.
 *
 * So: ownership follows the level a thing was onboarded at, and nothing else.
 * An Org Admin does not manage a unit's own connection even though they can
 * see it and outrank the person who made it. Reaching down into a unit's
 * integration would take a decision away from the tier that has the context
 * for it, and leave the unit's Admin looking at a screen that changes under
 * them. Governing what a unit MAY have is the org-wide grant; governing what
 * it DOES have is the unit's.
 *
 * Pure predicates, no I/O, shared by the Integrations page and its tests.
 */

/** The level an integration was onboarded at — connectors and MCP servers use
 *  the same two words (`ConnectorScope`, `McpScope`). */
export type IntegrationScope = "organization" | "business_unit";

export interface ManageScopeViewer {
  /** The viewer's effective platform role. */
  role: string | null;
  /** The Business Units they administer. Empty for an org-wide viewer. */
  businessUnitIds: string[];
}

export interface ManageableIntegration {
  scope: IntegrationScope;
  workspaceId: string | null | undefined;
}

/**
 * May this viewer manage this integration — onboard, re-key, disconnect?
 *
 * False is the answer for every role that is not one of the two admin tiers,
 * including the Project Admin: a project consumes integrations and configures
 * its own credentials against them, but it never onboards one.
 */
export function canManageIntegration(
  viewer: ManageScopeViewer,
  integration: ManageableIntegration,
): boolean {
  if (viewer.role === "org_admin") return integration.scope === "organization";
  if (viewer.role === "bu_admin") {
    if (integration.scope !== "business_unit") return false;
    // A unit-scoped integration with no unit on it belongs to nobody yet — it
    // is a placeholder the viewer is about to onboard INTO one of their units,
    // which the credentials dialog resolves. Manageable only if they hold one.
    if (!integration.workspaceId) return viewer.businessUnitIds.length > 0;
    return viewer.businessUnitIds.includes(String(integration.workspaceId));
  }
  return false;
}

/**
 * The level this viewer's newly onboarded integration lands at.
 *
 * Unchanged in substance from `onboardingScopeFor` in connector-scope.ts,
 * restated here because the manage rule above has to agree with it: an admin
 * must always be able to manage what they just onboarded, and the two rules
 * drifting apart is how you get a connection its own creator cannot re-key.
 */
export function onboardsAt(role: string | null): IntegrationScope {
  return role === "org_admin" ? "organization" : "business_unit";
}

/**
 * Why a manage control is absent — shown instead of a silently missing button.
 *
 * Returns null when the viewer may manage it, so a caller can use the presence
 * of a reason as the gate and never render an unexplained disabled control.
 */
export function manageDeniedReason(
  viewer: ManageScopeViewer,
  integration: ManageableIntegration,
  businessUnitLabel: string,
): string | null {
  if (canManageIntegration(viewer, integration)) return null;
  if (viewer.role === "org_admin") {
    return `Onboarded inside a ${businessUnitLabel.toLowerCase()} — its own Admin manages it. You control whether the ${businessUnitLabel.toLowerCase()} may have it at all.`;
  }
  if (viewer.role === "bu_admin") {
    return integration.scope === "organization"
      ? "Onboarded org-wide by an Organization Admin. You inherit it; you cannot change it."
      : `Onboarded by another ${businessUnitLabel.toLowerCase()}.`;
  }
  return "Only an Organization Admin or a Business Unit Admin onboards integrations.";
}
