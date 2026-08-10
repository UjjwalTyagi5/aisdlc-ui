"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Inbox } from "lucide-react";

import { cn } from "@/lib/utils";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingState } from "@/components/ui/loading-state";
import { ApprovalGateRow } from "@/components/app/approval-gate-row";
import { GovernanceApprovalRow } from "@/components/app/governance-approval-row";
import { listApprovals, type ApprovalFilters } from "@/lib/api/approvals";
import { listGovernanceApprovals } from "@/lib/api/governance-approvals";
import { OPEN_REQUEST_STATUSES } from "@/lib/schemas/governance-approval";
import { qk } from "@/lib/api/query-keys";
import { hasPermission } from "@/lib/auth/permissions";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { GOVERNANCE_APPROVER_ROLE } from "@/lib/governance";
import { ROLE_META } from "@/lib/roles";
import { useRawSession } from "@/components/auth/session-provider";
import { useActiveWorkspace } from "@/hooks/use-workspaces";
import { useAccessScope } from "@/hooks/use-access-scope";
import type { ApprovalGate, GovernanceApproval } from "@/lib/schemas";

type Scope = "mine" | "all";

export function ApprovalQueue() {
  const session = useRawSession();
  const queryClient = useQueryClient();
  const [scope, setScope] = React.useState<Scope>("mine");
  const { active: activeWorkspace } = useActiveWorkspace();
  const {
    isOrgWide,
    level: scopeLevel,
    bindings: accessBindings,
    scope: accessScope,
  } = useAccessScope();
  const identityId = accessScope?.identityId ?? null;
  const role = effectivePlatformRole(session);

  // Governance tier (org_admin, bu_admin) has NO agent access at all (PRD
  // §14.8) — no agent-run approval ever routes to them, so ApprovalGate
  // items (phase sign-offs, clarifications, outcomes) never apply and
  // aren't even worth fetching.
  const isGovernanceTier = role !== null && ROLE_META[role].governanceOnly;

  // Admins (admin:*) can hold every approval permission, so "Waiting on me"
  // already shows everything for them; the toggle still lets them flip to an
  // explicit "All pending" view for demos and oversight. Meaningless for a
  // governance-tier role (no ApprovalGate ever applies to them), so hidden.
  const isAdmin = !isGovernanceTier && hasPermission(session, "admin:*");

  // The queue no longer narrows by type — every kind of gate that reaches this
  // viewer is listed together. `filters` stays a typed object so the query key
  // and the endpoint keep their shape for a future filter.
  const filters: ApprovalFilters = {};

  const gatesQ = useQuery({
    queryKey: qk.approvals.list(filters),
    queryFn: () => listApprovals(filters),
    enabled: !isGovernanceTier,
  });

  // Governance approvals (project creation, model credentials, budget
  // increases) each route to exactly one role (lib/governance.ts) — an Org
  // Admin sees only budget_increase requests, a BU Admin only sees
  // project_creation/model_credential ones, never each other's, regardless of
  // the "All pending" toggle. Org Admin isn't scoped to one BU (they oversee
  // all of them), so they always fetch unscoped; a BU Admin's "mine"/"all"
  // toggle still chooses between their active BU and every BU.
  const canSeeGovernance = role === "org_admin" || role === "bu_admin";
  const governanceWorkspaceScope =
    scope === "mine" && role !== "org_admin" ? activeWorkspace?.id : undefined;
  const governanceQ = useQuery({
    queryKey: qk.governanceApprovals.list(governanceWorkspaceScope),
    queryFn: () => listGovernanceApprovals(governanceWorkspaceScope),
    enabled: canSeeGovernance,
  });

  // Role-scoping: "mine" keeps only gates the viewer can action.
  const visible = React.useMemo(() => {
    if (isGovernanceTier) return [];
    const all = gatesQ.data ?? [];
    if (scope === "all") return all;
    return all.filter((g) => hasPermission(session, g.requiredPermission));
  }, [gatesQ.data, scope, session, isGovernanceTier]);

  // Each governance approval routes to exactly one role — never show a BU
  // Admin's project/model requests to an Org Admin or vice versa.
  const visibleGovernance = (governanceQ.data ?? []).filter(
    (g) =>
      OPEN_REQUEST_STATUSES.includes(g.status) &&
      role !== null &&
      GOVERNANCE_APPROVER_ROLE[g.type] === role &&
      // Not the ones you raised. The endpoint already refuses self-approval, so
      // leaving them here offered a decision that could only ever fail — and it
      // only shows up for the types whose approver is a PEER rather than a tier
      // above (`cross_bu_assignment`), where the requester and the approver
      // hold the same role in different units.
      !(g.requestedById != null && g.requestedById === identityId),
  );

  // Sectioned by business unit, then by project within it — a governance
  // approval with no project (e.g. budget_increase) sits directly under its
  // BU heading.
  const governanceSections = React.useMemo(() => {
    const byWorkspace = new Map<string, { workspaceName: string; items: GovernanceApproval[] }>();
    for (const g of visibleGovernance) {
      const bucket = byWorkspace.get(g.workspaceId) ?? { workspaceName: g.workspaceName, items: [] };
      bucket.items.push(g);
      byWorkspace.set(g.workspaceId, bucket);
    }
    return [...byWorkspace.values()].map((bucket) => {
      const byProject = new Map<string, { projectName: string | null; items: GovernanceApproval[] }>();
      for (const g of bucket.items) {
        const key = g.projectId ?? "__none__";
        const sub = byProject.get(key) ?? { projectName: g.projectName, items: [] };
        sub.items.push(g);
        byProject.set(key, sub);
      }
      return { workspaceName: bucket.workspaceName, projectGroups: [...byProject.values()] };
    });
  }, [visibleGovernance]);

  // Optimistic resolve — drop the row from every cached approvals list. The
  // metrics strip recomputes automatically since it derives from this same
  // data (see `metrics` above), no separate invalidation needed.
  const handleResolved = (id: string) => {
    queryClient.setQueriesData<ApprovalGate[]>({ queryKey: qk.approvals.all() }, (old) =>
      Array.isArray(old) ? old.filter((g) => g.id !== id) : old,
    );
  };

  const handleGovernanceResolved = (id: string) => {
    queryClient.setQueriesData<GovernanceApproval[]>(
      { queryKey: ["governance-approvals", "list"] },
      (old) => (Array.isArray(old) ? old.filter((g) => g.id !== id) : old),
    );
    queryClient.invalidateQueries({ queryKey: qk.projects.all() });
  };

  const isLoading = gatesQ.isLoading || (canSeeGovernance && governanceQ.isLoading);
  const isEmpty = visible.length === 0 && visibleGovernance.length === 0;

  // A human-readable name for the boundary the queue was drawn from — the
  // server already filtered by it (app/api/approvals/route.ts), so the only
  // thing missing on screen is saying which one.
  const scopeSummary = React.useMemo(() => {
    if (isOrgWide) return null;
    const units = accessBindings.filter((b) => b.kind === "business_unit");
    const projects = accessBindings.filter((b) => b.kind === "project");
    if (scopeLevel === "business_unit" && units.length === 1) return units[0]!.scopeName;
    if (projects.length === 1) return projects[0]!.scopeName;
    if (projects.length > 1) return `your ${projects.length} projects`;
    if (units.length > 1) return `your ${units.length} business units`;
    return null;
  }, [accessBindings, isOrgWide, scopeLevel]);

  return (
    <div className="space-y-4">
      {/* Scope toggle — admins only; the type filter it used to sit beside is
          gone, so this row is empty for everyone else. */}
      {isAdmin && (
        <div className="flex flex-wrap items-center justify-end gap-3">
          <div className="border-line-soft inline-flex rounded-lg border p-0.5">
            <button
              type="button"
              onClick={() => setScope("mine")}
              className={cn(
                "rounded-md px-3 py-1.5 text-[12.5px] font-medium transition-colors",
                scope === "mine"
                  ? "bg-surface-2 text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
              aria-pressed={scope === "mine"}
            >
              Waiting on me
            </button>
            <button
              type="button"
              onClick={() => setScope("all")}
              className={cn(
                "rounded-md px-3 py-1.5 text-[12.5px] font-medium transition-colors",
                scope === "all"
                  ? "bg-surface-2 text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
              aria-pressed={scope === "all"}
            >
              All pending
            </button>
          </div>
        </div>
      )}

      {gatesQ.isError ? (
        <ApiErrorState
          title="Couldn't load approvals"
          error={
            gatesQ.error && "code" in gatesQ.error && "message" in gatesQ.error
              ? (gatesQ.error as { code: string; message: string; requestId?: string })
              : undefined
          }
          description={
            !(gatesQ.error && "code" in gatesQ.error)
              ? gatesQ.error instanceof Error
                ? gatesQ.error.message
                : "Unknown error."
              : undefined
          }
          onRetry={() => gatesQ.refetch()}
        />
      ) : isLoading ? (
        <LoadingState variant="list" rows={4} />
      ) : isEmpty ? (
        <EmptyState
          icon={Inbox}
          title="You're all caught up"
          description={
            // Naming the boundary is what makes an empty queue trustworthy: a
            // scoped viewer otherwise can't tell "nothing pending" from "the
            // filter hid it", and this queue is where that doubt is costliest.
            scopeSummary
              ? `Nothing in ${scopeSummary} is waiting on your decision right now. Phase sign-offs, agent questions, and governance approvals routed to your role will appear here.`
              : "Nothing is waiting on your decision right now. Phase sign-offs, agent questions, and governance approvals routed to your role will appear here."
          }
        />
      ) : (
        <div className="space-y-6">
          {governanceSections.length > 0 && (
            <div className="space-y-5">
              {governanceSections.map((section) => (
                <div key={section.workspaceName} className="space-y-3">
                  <h3 className="text-muted-foreground font-mono text-[11px] font-semibold uppercase tracking-[0.1em]">
                    {section.workspaceName}
                  </h3>
                  {section.projectGroups.map((group, i) => (
                    <div key={group.projectName ?? `__none__${i}`} className="space-y-2 pl-3">
                      {group.projectName && (
                        <p className="text-muted-foreground text-[12px] font-medium">
                          {group.projectName}
                        </p>
                      )}
                      <ul className="space-y-3">
                        {group.items.map((approval) => (
                          <GovernanceApprovalRow
                            key={approval.id}
                            approval={approval}
                            onResolved={handleGovernanceResolved}
                          />
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
          {visible.length > 0 && (
            <ul className="space-y-5">
              {visible.map((gate) => (
                <ApprovalGateRow key={gate.id} gate={gate} onResolved={handleResolved} />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
