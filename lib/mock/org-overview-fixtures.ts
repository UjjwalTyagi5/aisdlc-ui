/**
 * Organization rollup for the Org Admin dashboard — composed from the existing
 * fixture stores rather than seeded separately, so every figure agrees with the
 * page it links to (Business Units, Users, Model Management, Integrations,
 * Cost & Budget). Plain data + pure functions, server-safe.
 *
 * This is the DUMMY-DATA source; a real backend replaces the route-handler
 * body, not this shape.
 */
import { CONNECTORS, PROJECTS } from "@/mocks/fixtures";
import { listIdentities, listMembers, listWorkspaces } from "@/lib/mock/workspace-fixtures";
import { listAllModelProviders } from "@/lib/mock/model-fixtures";
import { visibleConnectorsForScope } from "@/lib/mock/connector-scope";
import { buildSpendSeries } from "@/lib/mock/cost-fixtures";
import type { OrgOverview } from "@/lib/schemas/org-overview";

const SERIES_MONTHS = 6;

/**
 * @param allowed the ids the viewer may read, or null when unbounded. Applied
 *   to every figure, not just the lists — an org-wide *count* handed to a
 *   Business Unit Admin would leak the size of the organization around them
 *   just as surely as the rows would.
 */
export function buildOrgOverview(
  allowed?: { workspaceIds: string[]; projectIds: string[] } | null,
): OrgOverview {
  const workspaceIds = allowed?.workspaceIds ?? null;

  const workspaces = listWorkspaces().filter(
    (w) => workspaceIds == null || workspaceIds.includes(String(w.id)),
  );

  const projects = PROJECTS.filter(
    (p) => !p.archived && (allowed == null || allowed.projectIds.includes(String(p.id))),
  );

  const connectors = visibleConnectorsForScope(CONNECTORS, null, workspaceIds);
  // "Connected" matches the Integrations page's own definition (installed and
  // not disconnected) so the two screens can't disagree about the same word.
  const connected = connectors.filter((c) => c.installed && c.health !== "disconnected");

  // Distinct people. Org-wide sees every identity; a scoped viewer sees only
  // those with a membership in a unit they can read, which is the same set the
  // Users page would compose for them.
  const userCount =
    workspaceIds == null
      ? listIdentities().length
      : new Set(
          // `memberCount` is denormalized per unit and would double-count
          // anyone in two of them, so this counts identities, not memberships.
          workspaces.flatMap((w) =>
            listMembers(String(w.id)).map((m) => String(m.identity.id)),
          ),
        ).size;

  const { months, series } = buildSpendSeries(SERIES_MONTHS, workspaceIds);

  return {
    userCount,
    modelProviderCount: listAllModelProviders(workspaceIds).length,
    connectorCount: connected.length,
    connectorTotalCount: connectors.length,
    businessUnitCount: workspaces.length,
    projectCount: projects.length,
    budgets: workspaces.map((w) => ({
      workspaceId: String(w.id),
      name: w.displayName,
      monthlyBudgetUsd: w.monthlyBudgetUsd ?? null,
      monthlySpendUsd: w.monthlySpendUsd,
      isActive: w.isActive,
    })),
    months,
    spendSeries: series,
    generatedAt: new Date().toISOString(),
  };
}
