"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";

import { PageTitle } from "@/components/app/page-title";
import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { ApprovalQueue } from "@/components/app/approval-queue";
import { ScopeChip } from "@/components/app/scope-indicator";
import { RaiseRequestDialog } from "@/components/requests/raise-request-dialog";
import { RequestDetailSheet } from "@/components/requests/request-detail-sheet";
import {
  RequestSummaryCards,
  countRequests,
} from "@/components/requests/request-summary-cards";
import { RequestTable } from "@/components/requests/request-table";
import { useSession } from "@/hooks/use-session";
import { useAccessScope } from "@/hooks/use-access-scope";
import { hasPermission } from "@/lib/auth/permissions";
import { listGovernanceApprovals } from "@/lib/api/governance-approvals";
import { listProjects } from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import { canRaiseRequest } from "@/lib/requests/routing";
import { OPEN_REQUEST_STATUSES } from "@/lib/schemas/governance-approval";
import type { GovernanceApproval } from "@/lib/schemas";

/**
 * Requests & Approvals — the personal queue, and the place you raise things.
 *
 * TWO LANES, ONE PAGE. PRD §33.2 separates them on every axis and this page
 * keeps that separation visible rather than flattening it:
 *
 *   An APPROVAL is an agent about to do something consequential now. It routes
 *   SIDEWAYS to the role that owns that agent, never up to a governance tier,
 *   and its only fallback is the Project Admin — audited as a fallback.
 *   Rendered by `ApprovalQueue`, unchanged.
 *
 *   A REQUEST is a person needing something they don't have. It routes UPWARD
 *   and climbs a tier at a time until someone can grant it. Everything under
 *   `components/requests/` is this lane.
 *
 * Merging them would breach "approvals never route to a governance tier" and
 * the "no approval laundering" guardrail — a consequential action would become
 * signable by someone who never had the standing to judge it. So the two share
 * a page and a set of counts, and nothing else.
 *
 * THREE TABS, THREE QUESTIONS. "Inbox" is what is waiting on you. "My
 * requests" is what you asked for and where it got to. "All" is everything in
 * scope. One list with a filter would answer all three worse than three lists
 * answer one each.
 */
export default function RequestsAndApprovalsPage() {
  const session = useSession({ required: true });
  const { scope, role, level, isOrgWide, bindings, managedBusinessUnitIds } = useAccessScope();

  const [raiseOpen, setRaiseOpen] = React.useState(false);
  const [selected, setSelected] = React.useState<GovernanceApproval | null>(null);

  const requestsQ = useQuery({
    queryKey: qk.governanceApprovals.list(),
    queryFn: () => listGovernanceApprovals(),
  });

  // Only for the Raise dialog's project picker — already scope-filtered
  // server-side, so this never widens what the person may file against.
  const projectsQ = useQuery({
    queryKey: qk.projects.list({ pageSize: 100 }),
    queryFn: () => listProjects({ pageSize: 100 }),
    staleTime: 60_000,
  });

  const requests = React.useMemo(() => requestsQ.data ?? [], [requestsQ.data]);
  const identityId = scope?.identityId ?? null;
  const counts = React.useMemo(
    () => countRequests(requests, identityId),
    [requests, identityId],
  );

  // Keep the open sheet in step with a refetch — after a decision it would
  // otherwise still be showing the pre-decision copy of the request.
  const selectedLive = React.useMemo(
    () => (selected ? (requests.find((r) => r.id === selected.id) ?? selected) : null),
    [requests, selected],
  );

  const inboxRequests = React.useMemo(
    () =>
      requests.filter(
        (r) =>
          OPEN_REQUEST_STATUSES.includes(r.status) &&
          role !== null &&
          r.currentApproverRole === role &&
          // Never your own — it escalates rather than self-approving (§33.2).
          !(identityId && r.requestedById === identityId),
      ),
    [requests, role, identityId],
  );

  const myRequests = React.useMemo(
    () => (identityId ? requests.filter((r) => r.requestedById === identityId) : []),
    [requests, identityId],
  );

  const canSeeQueue =
    hasPermission(session, "artifact:view") || hasPermission(session, "workspace:manage");
  if (!canSeeQueue) {
    return (
      <RestrictedAccess description="Requests & Approvals requires the artifact:view or workspace:manage permission. Ask your admin for access." />
    );
  }

  const unitBindings = bindings.filter((b) => b.kind === "business_unit");
  const projectBindings = bindings.filter((b) => b.kind === "project");
  const scopeName = isOrgWide
    ? null
    : level === "business_unit"
      ? ((managedBusinessUnitIds.length === 1
          ? unitBindings.find((b) => b.scopeId === managedBusinessUnitIds[0])?.scopeName
          : undefined) ?? `${managedBusinessUnitIds.length} business units`)
      : projectBindings.length === 1
        ? projectBindings[0]!.scopeName
        : `${projectBindings.length} projects`;

  const mayRaise = canRaiseRequest(role);

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      <header
        className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between"
        style={{
          animationName: "rise",
          animationDuration: "0.6s",
          animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
          animationFillMode: "both",
        }}
      >
        <div>
          <PageTitle>Requests &amp; Approvals</PageTitle>

          {/* A scoped viewer still needs the boundary named: "you're all caught
              up" must be legible as "in Payments", not "in the organisation".
              The chip itself is silent for an org-wide viewer, who has no
              narrower scope to be confused with (see ScopeChip). */}
          {scope !== null && (
            <div className="flex flex-wrap items-center gap-2">
              <ScopeChip kind={isOrgWide ? "organization" : level} name={scopeName} size="sm" />
            </div>
          )}
        </div>

        {/* Hidden for the Organization Admin — nothing sits above them to
            decide it. The endpoint refuses independently (403); this only
            stops the page offering a door that closes. */}
        {mayRaise && (
          <Button className="shrink-0 gap-1.5" onClick={() => setRaiseOpen(true)}>
            <Plus className="size-4" aria-hidden />
            Raise request
          </Button>
        )}
      </header>

      <RequestSummaryCards counts={counts} />

      <Tabs defaultValue="inbox" className="space-y-4">
        <TabsList>
          <TabsTrigger value="inbox">
            Inbox
            {inboxRequests.length > 0 && (
              <span className="bg-warning/15 text-warning ml-1.5 rounded-full px-1.5 font-mono text-[10px]">
                {inboxRequests.length}
              </span>
            )}
          </TabsTrigger>
          <TabsTrigger value="mine">
            My requests
            {myRequests.length > 0 && (
              <span className="text-muted-foreground ml-1.5 font-mono text-[10px]">
                {myRequests.length}
              </span>
            )}
          </TabsTrigger>
          <TabsTrigger value="all">All</TabsTrigger>
        </TabsList>

        {/* ── Inbox: requests routed to this role, then the agent gates ───── */}
        <TabsContent value="inbox" className="space-y-6">
          {requestsQ.isError ? (
            <ErrorState title="Couldn't load requests" onRetry={() => requestsQ.refetch()} />
          ) : requestsQ.isLoading ? (
            <LoadingState variant="list" rows={3} />
          ) : (
            inboxRequests.length > 0 && (
              <section className="space-y-2">
                <h2 className="text-muted-foreground font-mono text-[10px] tracking-[0.14em] uppercase">
                  Requests waiting on you
                </h2>
                <RequestTable
                  requests={inboxRequests}
                  onOpen={setSelected}
                  emptyTitle="Nothing waiting on you"
                  /* Named by ROLE, because "requests routed to your role" is
                     true of everyone and tells nobody what to expect. What
                     lands here differs sharply by tier, and an empty queue is
                     exactly when someone wants to know what would fill it. */
                  emptyDescription={
                    role === "org_admin"
                      ? "Onboarding a model provider org-wide, granting a connector or MCP server to a business unit, adding someone to the platform, a business unit's budget increase, and archiving a project all land here."
                      : role === "bu_admin"
                        ? "New projects in your business units, model credentials, connector and MCP requests from your Project Admins, and onboarding someone new all land here."
                        : role === "project_admin"
                          ? "Your contributors' asks land here — a model for the project, a connector or MCP server, agent access, and anyone needing onboarding."
                          : "Agent gates you own, and clarifications on runs you are part of, appear here."
                  }
                />
              </section>
            )
          )}

          {/* The approval lane, untouched — agent gates and clarifications. */}
          <ApprovalQueue />
        </TabsContent>

        <TabsContent value="mine">
          {requestsQ.isLoading ? (
            <LoadingState variant="list" rows={3} />
          ) : (
            <RequestTable
              requests={myRequests}
              onOpen={setSelected}
              emptyTitle="You haven't raised anything"
              emptyDescription={
                mayRaise
                  ? "Use Raise request when you need something you don't have — a model for your project, a connector or MCP server, agent access, budget headroom, or someone onboarded."
                  : "Organization Admins decide requests rather than raising them."
              }
            />
          )}
        </TabsContent>

        <TabsContent value="all">
          {requestsQ.isLoading ? (
            <LoadingState variant="list" rows={4} />
          ) : (
            <RequestTable
              requests={requests}
              onOpen={setSelected}
              emptyTitle="No requests in scope"
              emptyDescription="Requests raised in the business units and projects you can see appear here."
            />
          )}
        </TabsContent>
      </Tabs>

      <RaiseRequestDialog
        open={raiseOpen}
        onOpenChange={setRaiseOpen}
        projects={projectsQ.data?.items ?? []}
      />
      <RequestDetailSheet
        request={selectedLive}
        open={selected !== null}
        onOpenChange={(o) => !o && setSelected(null)}
      />
    </div>
  );
}
