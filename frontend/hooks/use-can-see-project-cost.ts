"use client";

import { useQuery } from "@tanstack/react-query";

import { useAccessScope } from "@/hooks/use-access-scope";
import { getProject } from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import type { ProjectId } from "@/lib/schemas";

/**
 * May the viewer see this project's spend?
 *
 * True for an organization-scoped role, the Project Admin of THIS project, and
 * the admin of the Business Unit it belongs to. False for everyone else,
 * including a Project Admin of some other project.
 *
 * NARROWS PRD §34.5, which reads "cost visibility is deliberately not a
 * privilege — every builder sees their own project's spend read-only". Treating
 * spend as commercially sensitive rather than ambient is a deliberate product
 * decision taken knowing the PRD says otherwise.
 *
 * ONE PREDICATE, THREE SURFACES — the Cost tab, the Cost page, and the spend
 * panel on Overview. It lives here because the first version of this gate did
 * not: the tab and the page were restricted while the Overview kept plotting
 * the same figures, so the whole month's spend stayed one scroll away from
 * anyone the gate was meant to exclude. A rule enforced in two places out of
 * three is not a rule.
 *
 * Presentation only. `hasPermission()` remains the action gate and the backend
 * the enforcement boundary (PRD §14.10) — this hides what the viewer may not
 * act on; it does not secure the data.
 */
export function useCanSeeProjectCost(projectId: string): boolean {
  const scope = useAccessScope();

  // Fetched under the same key by the project layout and the overview page, so
  // this is a cache read rather than a third request. Needed for the project's
  // parent unit, which is what makes a BU Admin an admin of it.
  const projectQ = useQuery({
    queryKey: qk.projects.detail(projectId as ProjectId),
    queryFn: () => getProject(projectId as ProjectId),
    staleTime: 60_000,
  });

  // Fails closed while either request is in flight: spend that appears and then
  // disappears has already been read.
  return (
    scope.isOrgWide ||
    scope.canManageProject(projectId) ||
    scope.canManageBusinessUnit(projectQ.data?.workspaceId ?? null)
  );
}
