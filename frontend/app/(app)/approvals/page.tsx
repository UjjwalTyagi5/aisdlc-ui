"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";

import { PageTitle } from "@/components/app/page-title";
import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { ApprovalQueue } from "@/components/app/approval-queue";
import { ApprovalGateRow } from "@/components/app/approval-gate-row";
import { ScopeChip } from "@/components/app/scope-indicator";
import { RaiseRequestDialog } from "@/components/requests/raise-request-dialog";
import { RequestDetailSheet } from "@/components/requests/request-detail-sheet";
import {
  RequestSummaryCards,
  countRequests,
  raisedBy,
} from "@/components/requests/request-summary-cards";
import { RequestTable } from "@/components/requests/request-table";
import { useSession } from "@/hooks/use-session";
import { useAccessScope } from "@/hooks/use-access-scope";
import { ROLE_META } from "@/lib/roles";
import { hasPermission } from "@/lib/auth/permissions";
import { listApprovals } from "@/lib/api/approvals";
import { listGovernanceApprovals } from "@/lib/api/governance-approvals";
import { listProjects } from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import { canRaiseRequest } from "@/lib/requests/routing";
import { OPEN_REQUEST_STATUSES } from "@/lib/schemas/governance-approval";
import type { ApprovalGate, GovernanceApproval } from "@/lib/schemas";

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
 *
 * THE ORGANIZATION ADMIN GETS ONE. They sit at the top of the routing ladder:
 * nothing is above them to decide their asks, so `canRaiseRequest` is false for
 * them and "My requests" is structurally empty rather than merely empty today.
 * With only the inbox left, the tab bar names nothing worth choosing between,
 * so it goes too and the inbox is simply the page.
 */
/** One lane of derived approvals under a heading, or nothing at all.
 *
 * Deliberately NOT `ApprovalQueue`: that component owns the Inbox's own fetch, its
 * mine/all toggle and its governance rows. Here the list is already filtered by the
 * tab, and a second scope toggle inside a tab that is itself a scope would be a
 * control arguing with its container.
 */
function AwaitingLane({
  gates,
  heading,
  onResolved,
}: {
  gates: ApprovalGate[];
  heading: string;
  onResolved: () => void;
}) {
  if (gates.length === 0) return null;
  return (
    <section className="space-y-2">
      <h2 className="text-muted-foreground font-mono text-[10px] tracking-[0.14em] uppercase">
        {heading}
      </h2>
      <ul className="space-y-4">
        {gates.map((g) => (
          <ApprovalGateRow key={g.id} gate={g} onResolved={onResolved} />
        ))}
      </ul>
    </section>
  );
}

export default function RequestsAndApprovalsPage() {
  const session = useSession({ required: true });
  const queryClient = useQueryClient();
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

  // Governance tier (org_admin, bu_admin) holds no agent access at all (PRD §14.8),
  // so no gate or document approval ever routes to them — same test `ApprovalQueue`
  // makes before deciding whether to fetch.
  const isGovernanceTier = role !== null && ROLE_META[role].governanceOnly;
  const identityId = scope?.identityId ?? null;
  // THE SAME QUERY THE INBOX BELOW RUNS, deduped by react-query on the shared key —
  // so the tiles and the list can never be counting different things. A governance-tier
  // viewer never sees gates (PRD §14.8), and `ApprovalQueue` skips the fetch for them
  // for the same reason.
  const gatesQ = useQuery({
    queryKey: qk.approvals.list({}),
    queryFn: () => listApprovals({}),
    enabled: !isGovernanceTier,
  });
  const awaiting = React.useMemo(
    () => (isGovernanceTier ? [] : (gatesQ.data ?? [])),
    [gatesQ.data, isGovernanceTier],
  );
  const counts = React.useMemo(
    () => countRequests(requests, identityId, awaiting),
    [requests, identityId, awaiting],
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
    () => raisedBy(requests, identityId),
    [requests, identityId],
  );

  // THE SAME TWO LANES THE TILES COUNT. Counting documents into "Raised by me" without
  // showing them here would have been the original bug moved one level down: the tile
  // reading 1 above a tab saying "You haven't raised anything". A document you uploaded
  // IS something you raised — it is waiting on an owner exactly like a request is.
  const myAwaiting = React.useMemo(
    () => raisedBy(awaiting, identityId),
    [awaiting, identityId],
  );

  const refreshApprovals = React.useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: qk.approvals.list({}) });
  }, [queryClient]);

  const canSeeQueue =
    hasPermission(session, "artifact:view") || hasPermission(session, "workspace:manage");
  if (!canSeeQueue) {
    return (
      <RestrictedAccess description="Requests & Approvals requires the artifact:view or workspace:manage permission. Ask your admin for access." />
    );
  }

  const unitBindings = bindings.filter((b) => b.kind === "business_unit");
  const projectBindings = bindings.filter((b) => b.kind === "project");
  // NAMED FROM WHERE YOU ARE BOUND, not from what you administer — those are two
  // different questions and this chip asks the first. A Project Admin bound at
  // business-unit scope administers no unit by design (that split is what keeps Users
  // and Roles & Access out of their nav), so `managedBusinessUnitIds` is empty for them
  // and the chip read "BUSINESS UNIT / 0 business units" — a scope indicator reporting
  // that the viewer is nowhere. They are in Lending; they simply do not run it.
  const scopeName = isOrgWide
    ? null
    : level === "business_unit"
      ? ((managedBusinessUnitIds.length === 1
          ? unitBindings.find((b) => b.scopeId === managedBusinessUnitIds[0])?.scopeName
          : undefined) ??
        (unitBindings.length === 1
          ? unitBindings[0]!.scopeName
          : `${unitBindings.length} business units`))
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
        {/* SHOWN TO EVERYONE, ORG ADMINS INCLUDED. These three tabs used to be hidden
            from an Org Admin on the reasoning that their inbox was the whole story. It
            is not: the inbox only holds requests routed to YOUR role and deliberately
            excludes your own, so an Org Admin who raised anything had no lane in which
            it could appear — they saw an empty page and no tab to click. Found while
            checking why a document deletion "never reached" this screen. */}
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

        <TabsContent value="mine" className="space-y-6">
            {requestsQ.isLoading ? (
              <LoadingState variant="list" rows={3} />
            ) : (
              // The empty state belongs to the WHOLE tab, so it is suppressed when the
              // other lane has something — "You haven't raised anything" above a
              // document you uploaded five minutes ago is the contradiction this fix is
              // about, not a smaller version of it.
              (myRequests.length > 0 || myAwaiting.length === 0) && (
                <RequestTable
                  requests={myRequests}
                  onOpen={setSelected}
                  emptyTitle="You haven't raised anything"
                  emptyDescription="Use Raise request when you need something you don't have — a model for your project, a connector or MCP server, agent access, budget headroom, or someone onboarded."
                />
              )
            )}
            <AwaitingLane
              gates={myAwaiting}
              heading="Documents you put forward"
              onResolved={refreshApprovals}
            />
        </TabsContent>

        <TabsContent value="all" className="space-y-6">
            {requestsQ.isLoading ? (
              <LoadingState variant="list" rows={4} />
            ) : (
              (requests.length > 0 || awaiting.length === 0) && (
                <RequestTable
                  requests={requests}
                  onOpen={setSelected}
                  emptyTitle="No requests in scope"
                  emptyDescription="Requests raised in the business units and projects you can see appear here."
                />
              )
            )}
            <AwaitingLane
              gates={awaiting}
              heading="Awaiting a decision"
              onResolved={refreshApprovals}
            />
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
