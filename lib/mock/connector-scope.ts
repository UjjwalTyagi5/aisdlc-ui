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
import type { Connector } from "@/lib/schemas/connector";

/**
 * What a Business Unit can see: every org-wide connector, plus the ones
 * onboarded into that unit. A null `workspaceId` means the caller has no unit
 * context and gets the whole tenant list — the onboarding wizard and the
 * dashboard health tile both legitimately want that.
 *
 * Inheritance is one-way by construction: a unit never sees a sibling unit's
 * connectors, and an org-wide connector is never hidden from a unit.
 */
export function visibleConnectors(
  connectors: Connector[],
  workspaceId: string | null | undefined,
): Connector[] {
  if (!workspaceId) return connectors;
  return connectors.filter(
    (c) => c.scope === "organization" || c.workspaceId === workspaceId,
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
  return connectors.filter(
    (c) =>
      c.scope === "organization" ||
      (c.workspaceId != null && allowedWorkspaceIds.includes(String(c.workspaceId))),
  );
}

/** The level an admin's newly onboarded connector lands at. */
export function onboardingScopeFor(
  role: string | null,
): { scope: Connector["scope"]; requiresWorkspace: boolean } {
  // Only an Org Admin onboards org-wide; everyone else who may onboard at all
  // does so into their own unit, which is why the BU case needs a workspace.
  return role === "org_admin"
    ? { scope: "organization", requiresWorkspace: false }
    : { scope: "business_unit", requiresWorkspace: true };
}
