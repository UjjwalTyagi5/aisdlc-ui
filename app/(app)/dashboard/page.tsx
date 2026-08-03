"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  BrainCircuit,
  Building2,
  CircleDollarSign,
  FolderKanban,
  Inbox,
  Layers,
  Plug,
  ShieldCheck,
  Users,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";

import { cn } from "@/lib/utils";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingState } from "@/components/ui/loading-state";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  AttentionList,
  type AttentionItem,
} from "@/components/app/attention-list";
import {
  StatTile,
  StatTileGrid,
  budgetTone,
  usd,
} from "@/components/app/stat-tile";
import { PersonaBadge, ScopeChip } from "@/components/app/scope-indicator";
import { SpendPanel } from "@/components/app/spend-panel";
import { ActiveBadge } from "@/components/app/active-badge";
import { NoScopeAccess } from "@/components/auth/scope-empty-state";
import { useSession } from "@/hooks/use-session";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { useAccessScope } from "@/hooks/use-access-scope";
import {
  dashboardVariant,
  effectivePlatformRole,
} from "@/lib/auth/effective-role";
import { ROLE_META } from "@/lib/roles";
import { PHASE_LABEL } from "@/lib/agents";
import { TRACK_META } from "@/lib/tracks";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import { listApprovals } from "@/lib/api/approvals";
import { listGovernanceApprovals } from "@/lib/api/governance-approvals";
import { listConnectors } from "@/lib/api/connectors";
import { listProjects } from "@/lib/api/projects";
import { getOrgOverview } from "@/lib/api/org";
import { qk } from "@/lib/api/query-keys";
import type { Project } from "@/lib/schemas";

/**
 * Dashboard — PRD §36 ("default landing on sign-in") and §15.2–§15.11.
 *
 * One screen, four leads — NOT four dashboard products:
 *
 *   Organization Admin   every business unit with budget usage and project
 *                        count; total spend vs. budget; org-wide attention list
 *   Business Unit Admin  its own unit's projects, budget, governance queue and
 *                        attention list — and nothing from a sibling unit
 *   Project Admin        the projects it administers, their stage, pending
 *                        approvals and spend vs. cap
 *   Contributors         the same projects, led by the stage in front of them
 *                        rather than by budget and governance
 *
 * Every number here now comes from an endpoint the server has already scoped
 * (`lib/auth/access-scope.ts`), so the difference between variants is what is
 * EMPHASISED, not what is fetched — a Business Unit Admin's "3 projects" is
 * their unit's three because `/api/projects` returned three, not because this
 * page filtered them afterwards. That matters: a client-side filter over an
 * org-wide payload still ships every other unit's data to the browser.
 *
 * §15.1 is why this stays one page: the platform has one fixed page set, and
 * "what changes by role is only which pages a person can open and what they can
 * do inside them".
 */

/** The stage a project is currently sitting on — first non-terminal entry. */
function currentStage(project: Project) {
  const active = project.pipeline.find(
    (p) => p.status === "running" || p.status === "awaiting_approval",
  );
  return active ?? project.pipeline.find((p) => p.status === "queued") ?? null;
}

export default function DashboardPage() {
  const session = useSession({ required: true });
  const role = effectivePlatformRole(session);
  const variant = dashboardVariant(session);
  const {
    scope,
    isOrgWide,
    level,
    managedBusinessUnitIds,
    managedProjectIds,
    bindings,
    isLoading: scopeLoading,
  } = useAccessScope();

  // Governance tier (org_admin, bu_admin) has NO agent access at all (PRD
  // §14.8) — no agent-run approval ever routes to them, so it's not even
  // worth fetching.
  const isGovernanceTier = role !== null && ROLE_META[role].governanceOnly;
  const isOrg = variant === "organization";

  const projectsQ = useQuery({
    queryKey: qk.projects.list({ dashboard: true }),
    queryFn: () => listProjects({ pageSize: 50 }),
  });

  const workspacesQ = useWorkspaces();

  const approvalsQ = useQuery({
    queryKey: qk.approvals.list({ dashboard: true }),
    queryFn: () => listApprovals({}),
    enabled: !isGovernanceTier,
  });

  // The governance tier's queue is the mirror image: no agent gates, but
  // project-creation / model-credential / budget requests routed to them. Their
  // "waiting on you" tile counted zero without this, which read as "nothing to
  // do" while a project sat unapproved.
  const governanceQ = useQuery({
    queryKey: qk.governanceApprovals.list(),
    queryFn: () => listGovernanceApprovals(),
    enabled: isGovernanceTier,
  });

  const connectorsQ = useQuery({
    queryKey: qk.connectors.list(),
    queryFn: () => listConnectors(),
  });

  // The org-wide inventory (providers, connectors, people) and the spend
  // history behind them. One request rather than three fan-outs — the counts
  // need de-duplication across scopes that only the server can do correctly
  // (see lib/schemas/org-overview.ts). Fetched only for the variant that
  // renders it.
  const overviewQ = useQuery({
    queryKey: qk.org.overview(),
    queryFn: getOrgOverview,
    enabled: isOrg,
    staleTime: 60_000,
  });
  const overview = overviewQ.data;

  const projects = React.useMemo(
    () => (projectsQ.data?.items ?? []).filter((p) => !p.archived),
    [projectsQ.data],
  );
  const units = React.useMemo(
    () => (workspacesQ.data ?? []).filter((w) => w.status === "active"),
    [workspacesQ.data],
  );
  const approvals = React.useMemo(
    () => (isGovernanceTier ? [] : (approvalsQ.data ?? [])),
    [approvalsQ.data, isGovernanceTier],
  );
  const governanceApprovals = React.useMemo(
    () => (governanceQ.data ?? []).filter((g) => g.status === "pending"),
    [governanceQ.data],
  );
  const connectors = React.useMemo(() => connectorsQ.data ?? [], [connectorsQ.data]);

  // The single count the "waiting on you" tile shows — the governance tier's
  // queue and the delivery tier's are disjoint by construction, so summing them
  // is safe and gives one honest number per persona.
  const waitingCount = approvals.length + governanceApprovals.length;

  // ── Roll-ups ───────────────────────────────────────────────────────────────
  const unitSpend = units.reduce((n, w) => n + w.monthlySpendUsd, 0);
  const unitBudget = units.reduce((n, w) => n + (w.monthlyBudgetUsd ?? 0), 0);

  const projectSpend = projects.reduce((n, p) => n + (p.monthlySpendUsd ?? 0), 0);
  const projectBudget = projects.reduce((n, p) => n + (p.monthlyBudgetUsd ?? 0), 0);

  // Governance variants answer for their units' caps; delivery variants for
  // their own projects'. Both are already scope-filtered above.
  const governanceView = isOrg || variant === "business_unit";
  const spend = governanceView ? unitSpend : projectSpend;
  const budget = governanceView ? unitBudget : projectBudget;

  // ── Scope labels ───────────────────────────────────────────────────────────
  const soleUnit =
    managedBusinessUnitIds.length === 1
      ? units.find((u) => String(u.id) === managedBusinessUnitIds[0])
      : undefined;
  const contextName = isOrg
    ? null
    : variant === "business_unit"
      ? (soleUnit?.displayName ??
        (managedBusinessUnitIds.length > 1
          ? `${managedBusinessUnitIds.length} ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}`
          : null))
      : projects.length === 1
        ? projects[0]!.name
        : `${projects.length} ${projects.length === 1 ? "project" : "projects"}`;

  // ── Attention list (PRD §36: overdue approvals, budgets near/over cap,
  //    broken connectors; §37 adds integration errors) ────────────────────────
  const attention = React.useMemo<AttentionItem[]>(() => {
    const items: AttentionItem[] = [];
    const now = Date.now();

    // Overdue approvals — past their SLA deadline.
    for (const gate of approvals) {
      if (!gate.deadline) continue;
      const due = new Date(gate.deadline).getTime();
      if (due >= now) continue;
      items.push({
        id: `approval-${gate.id}`,
        kind: "overdue_approval",
        title: gate.title,
        detail: `${gate.projectName} · waiting on ${gate.waitingForRole}`,
        href: "/approvals",
        meta: `${formatDistanceToNow(due)} overdue`,
      });
    }

    // Governance requests aging in a governance-tier queue — the equivalent
    // pressure for a role that never sees an agent gate. Anything older than a
    // day is surfaced; there is no SLA field on these yet.
    for (const g of governanceApprovals) {
      const age = now - new Date(g.requestedAt).getTime();
      if (age < 24 * 60 * 60 * 1000) continue;
      items.push({
        id: `governance-${g.id}`,
        kind: "overdue_approval",
        title: g.title,
        detail: `${g.workspaceName} · requested by ${g.requestedBy}`,
        href: "/approvals",
        meta: `${formatDistanceToNow(new Date(g.requestedAt))} old`,
      });
    }

    // Budgets near or over cap — governance variants see units, delivery sees
    // their own projects. Thresholds mirror PRD §34.5.
    if (governanceView) {
      for (const w of units) {
        const cap = w.monthlyBudgetUsd;
        if (!cap) continue;
        const ratio = w.monthlySpendUsd / cap;
        if (ratio < 0.8) continue;
        items.push({
          id: `budget-${w.id}`,
          kind: ratio >= 1 ? "budget_over_cap" : "budget_near_cap",
          title: w.displayName,
          detail: `${usd(w.monthlySpendUsd)} of ${usd(cap)} monthly cap`,
          href: "/cost",
          meta: `${Math.round(ratio * 100)}%`,
        });
      }
    } else {
      for (const p of projects) {
        const cap = p.monthlyBudgetUsd;
        if (!cap) continue;
        const ratio = (p.monthlySpendUsd ?? 0) / cap;
        if (ratio < 0.8) continue;
        items.push({
          id: `budget-${p.id}`,
          kind: ratio >= 1 ? "budget_over_cap" : "budget_near_cap",
          title: p.name,
          detail: `${usd(p.monthlySpendUsd ?? 0)} of ${usd(cap)} monthly cap`,
          href: `/projects/${p.id}`,
          meta: `${Math.round(ratio * 100)}%`,
        });
      }
    }

    // Broken connectors — an integration error surfaces here (PRD §37). The
    // list is already scoped to the viewer's units, so a sibling unit's broken
    // Slack no longer shows up as this viewer's problem.
    for (const c of connectors) {
      if (c.health === "healthy") continue;
      items.push({
        id: `connector-${c.kind}`,
        kind: "broken_connector",
        title: `${c.kind.replace(/_/g, " ")} connection`,
        detail:
          c.health === "disconnected"
            ? "Disconnected — agent actions needing this connector will pause at their gate."
            : "Degraded — recent calls are failing intermittently.",
        href: "/integrations",
      });
    }

    return items;
  }, [approvals, governanceApprovals, connectors, projects, units, governanceView]);

  const isLoading =
    projectsQ.isLoading || workspacesQ.isLoading || approvalsQ.isLoading || scopeLoading;

  const scopeLine = isOrg
    ? `${units.length} ${units.length === 1 ? "business unit" : "business units"} · ${projects.length} active projects`
    : variant === "business_unit"
      ? `${projects.length} ${projects.length === 1 ? "project" : "projects"} in ${soleUnit?.displayName ?? `your ${BUSINESS_UNIT_LABEL.toLowerCase()}`}`
      : variant === "project_admin"
        ? `${managedProjectIds.length} ${managedProjectIds.length === 1 ? "project" : "projects"} you administer`
        : `${projects.length} ${projects.length === 1 ? "project" : "projects"} assigned to you`;

  // Resolved, bound to nothing: say so instead of rendering a dashboard of
  // zeroes, which is indistinguishable from a broken page.
  const unbound =
    scope !== null && !isOrgWide && units.length === 0 && projects.length === 0;

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header
        className="flex flex-col items-start gap-1"
        style={{
          animationName: "rise",
          animationDuration: "0.6s",
          animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
          animationFillMode: "both",
        }}
      >
        <div className="text-brand-bright mb-2.5 flex items-center gap-2 font-mono text-[11px] tracking-[0.14em] uppercase">
          <span className="bg-brand-bright inline-block h-px w-5" aria-hidden />
          {role ? ROLE_META[role].label : "Overview"}
        </div>
        <h1 className="font-display text-[38px] leading-[1.02] font-bold tracking-[-0.03em]">
          Dashboard
        </h1>

        {/* The boundary this dashboard is drawn inside, stated on the page
            itself and not only in the top bar — the numbers below are
            meaningless without it. */}
        {scope !== null && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <ScopeChip
              kind={isOrgWide ? "organization" : level}
              name={contextName}
              size="sm"
            />
            <PersonaBadge role={role} showScope />
          </div>
        )}

        <p className="text-muted-foreground mt-2 max-w-[620px] text-[14px]">
          {scopeLine}. Spend against budget, work in progress, and everything
          waiting on your decision.
        </p>
      </header>

      {isLoading ? (
        <LoadingState variant="card" />
      ) : unbound ? (
        <NoScopeAccess resource="projects" />
      ) : (
        <>
          {/* ── Summary tiles ────────────────────────────────────────────── */}
          <StatTileGrid>
            {isOrg && (
              <StatTile
                label={BUSINESS_UNIT_LABEL_PLURAL}
                value={String(units.length)}
                sub={`${units.reduce((n, w) => n + w.memberCount, 0)} members`}
                icon={Building2}
                href="/workspaces"
              />
            )}

            {/* A Business Unit Admin's own unit, with the members they govern —
                the equivalent anchor tile for a single-unit scope. */}
            {variant === "business_unit" && soleUnit && (
              <StatTile
                label={BUSINESS_UNIT_LABEL}
                value={soleUnit.displayName}
                sub={`${soleUnit.memberCount} members · ${soleUnit.costCenter ?? "no cost centre"}`}
                icon={Building2}
                href={`/workspaces/${soleUnit.id}`}
              />
            )}

            <StatTile
              label={variant === "project_admin" ? "Projects you administer" : "Active projects"}
              value={String(
                variant === "project_admin" ? managedProjectIds.length : projects.length,
              )}
              sub={
                isOrg
                  ? "across every business unit"
                  : variant === "business_unit"
                    ? `in ${soleUnit?.displayName ?? "your unit"}`
                    : "in your scope"
              }
              icon={FolderKanban}
              href="/projects"
            />

            <StatTile
              label="Spend this month"
              value={usd(spend)}
              sub={budget > 0 ? `of ${usd(budget)} cap` : "no cap set"}
              icon={CircleDollarSign}
              progress={budget > 0 ? spend / budget : undefined}
              tone={budgetTone(spend, budget)}
              href="/cost"
            />

            {/* Org-wide inventory. Only for the organization variant: a
                Business Unit Admin's "12 connectors" would count integrations
                they neither own nor can reach, which is a worse answer than no
                tile at all. Each links to the page that governs the thing it
                counts, so the number is a way in rather than trivia. */}
            {isOrg && (
              <>
                <StatTile
                  label="Model providers"
                  value={overview ? String(overview.modelProviderCount) : "—"}
                  sub="org-wide and unit-scoped"
                  icon={BrainCircuit}
                  href="/admin/models"
                />
                <StatTile
                  label="Connectors"
                  value={overview ? String(overview.connectorCount) : "—"}
                  sub={
                    overview
                      ? `of ${overview.connectorTotalCount} available, connected`
                      : "connected"
                  }
                  icon={Plug}
                  href="/integrations"
                />
                <StatTile
                  label="People"
                  value={overview ? String(overview.userCount) : "—"}
                  sub="across every business unit"
                  icon={Users}
                  href="/users"
                />
              </>
            )}

            <StatTile
              label="Waiting on you"
              value={String(waitingCount)}
              sub={
                waitingCount === 0
                  ? "queue is clear"
                  : isGovernanceTier
                    ? "governance requests"
                    : "approvals & clarifications"
              }
              icon={Inbox}
              tone={waitingCount > 0 ? "warning" : "default"}
              href="/approvals"
            />
          </StatTileGrid>

          {/* ── Spend over time ──────────────────────────────────────────
              Full width and above the two-column row: the tiles say what this
              month costs, and the only question that follows is whether that
              is normal — which a single number cannot answer. */}
          {/* Both governance tiers, not just the Org Admin: a Business Unit
              Admin has a budget to answer for too, and the endpoint already
              bounds them to their own units — so the same panel serves both
              and simply contains less for one of them. */}
          {governanceView && (
            <SpendPanel workspaces={units.map((w) => ({ id: String(w.id), displayName: w.displayName }))} />
          )}

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1.55fr_1fr]">
            {/* ── Work in progress ──────────────────────────────────────── */}
            <section
              aria-labelledby="wip-heading"
              className="border-line-soft bg-panel-elevated rounded-xl border"
            >
              <div className="border-line-soft flex items-center justify-between border-b px-4 py-3">
                <h2
                  id="wip-heading"
                  className="font-display text-[13px] font-semibold tracking-tight"
                >
                  {isOrg
                    ? BUSINESS_UNIT_LABEL_PLURAL
                    : variant === "contributor"
                      ? "Your work in progress"
                      : "Work in progress"}
                </h2>
                <Link
                  href={isOrg ? "/workspaces" : "/projects"}
                  className="text-muted-foreground hover:text-foreground font-mono text-[11px] transition-colors"
                >
                  View all →
                </Link>
              </div>

              {/* Organization Admin sees every unit with budget usage and
                  project count (PRD §15.2). */}
              {isOrg ? (
                units.length === 0 ? (
                  <EmptyState
                    icon={Building2}
                    title={`No ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} yet`}
                    description="Create the first business unit to give budgets, connections and projects a home."
                  />
                ) : (
                  <ul className="divide-line-soft divide-y">
                    {units.map((w) => {
                      const cap = w.monthlyBudgetUsd ?? 0;
                      const ratio = cap > 0 ? w.monthlySpendUsd / cap : 0;
                      return (
                        <li key={w.id}>
                          <Link
                            href={`/workspaces/${w.id}`}
                            className="hover:bg-surface-1 flex items-center gap-4 px-4 py-3 transition-colors"
                          >
                            <span className="min-w-0 flex-1">
                              <span className="flex items-center gap-2 text-[13px] font-medium">
                                {w.displayName}
                                <ActiveBadge isActive={w.isActive} />
                              </span>
                              <span className="text-muted-foreground mt-0.5 flex flex-wrap items-center gap-x-2 text-[11.5px]">
                                <span>{w.projectCount} projects</span>
                                <span aria-hidden>·</span>
                                <span>{w.memberCount} members</span>
                                {w.costCenter && (
                                  <>
                                    <span aria-hidden>·</span>
                                    <span className="font-mono">{w.costCenter}</span>
                                  </>
                                )}
                              </span>
                            </span>

                            <span className="w-32 shrink-0 text-right">
                              <span
                                className={cn(
                                  "block font-mono text-[12px]",
                                  ratio >= 1
                                    ? "text-destructive"
                                    : ratio >= 0.8
                                      ? "text-warning"
                                      : "text-foreground",
                                )}
                              >
                                {usd(w.monthlySpendUsd)}
                              </span>
                              {cap > 0 && (
                                <span className="bg-surface-2 mt-1.5 block h-1 w-full overflow-hidden rounded-full">
                                  <span
                                    className={cn(
                                      "block h-full rounded-full",
                                      ratio >= 1
                                        ? "bg-destructive"
                                        : ratio >= 0.8
                                          ? "bg-warning"
                                          : "bg-primary",
                                    )}
                                    style={{
                                      width: `${Math.min(100, ratio * 100)}%`,
                                    }}
                                  />
                                </span>
                              )}
                            </span>
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                )
              ) : projects.length === 0 ? (
                <EmptyState
                  icon={FolderKanban}
                  title={
                    variant === "business_unit"
                      ? `No projects in ${soleUnit?.displayName ?? `your ${BUSINESS_UNIT_LABEL.toLowerCase()}`} yet`
                      : "No projects assigned to you yet"
                  }
                  description={
                    variant === "business_unit"
                      ? "Projects created in this business unit appear here with their current stage and pending approvals."
                      : "Once you're assigned to a project it appears here with its current stage and pending approvals."
                  }
                />
              ) : (
                <ul className="divide-line-soft divide-y">
                  {projects.map((p) => {
                    const stage = currentStage(p);
                    const track = TRACK_META[p.track];
                    const cap = p.monthlyBudgetUsd ?? 0;
                    const ratio = cap > 0 ? (p.monthlySpendUsd ?? 0) / cap : 0;
                    const administers = managedProjectIds.includes(String(p.id));
                    return (
                      <li key={p.id}>
                        <Link
                          href={`/projects/${p.id}`}
                          className="hover:bg-surface-1 flex items-center gap-4 px-4 py-3 transition-colors"
                        >
                          <span className="min-w-0 flex-1">
                            <span className="flex flex-wrap items-center gap-2">
                              <span className="text-[13px] font-medium">{p.name}</span>
                              <span className="border-line-soft text-muted-foreground inline-flex items-center gap-1 rounded-full border px-1.5 py-px font-mono text-[10px]">
                                <Layers className="size-2.5" aria-hidden />
                                Track {track.number}
                              </span>
                              {/* Which of these projects are yours to run, for
                                  a Project Admin who also contributes
                                  elsewhere — the two look identical otherwise. */}
                              {administers && !isOrg && (
                                <span className="border-brand-bright/35 bg-brand-bright/10 text-brand-bright inline-flex items-center gap-1 rounded-full border px-1.5 py-px font-mono text-[10px]">
                                  <ShieldCheck className="size-2.5" aria-hidden />
                                  You administer
                                </span>
                              )}
                            </span>
                            <span className="text-muted-foreground mt-0.5 block text-[11.5px]">
                              {stage
                                ? `${PHASE_LABEL[stage.phase]} · ${stage.status.replace(/_/g, " ")}`
                                : "All stages complete"}
                              {p.lastActivityAt && (
                                <>
                                  {" · "}
                                  {formatDistanceToNow(new Date(p.lastActivityAt), {
                                    addSuffix: true,
                                  })}
                                </>
                              )}
                            </span>
                          </span>

                          {stage && (
                            <StatusBadge status={stage.status} className="shrink-0" />
                          )}

                          {cap > 0 && (
                            <span
                              className={cn(
                                "hidden w-20 shrink-0 text-right font-mono text-[12px] sm:block",
                                ratio >= 1
                                  ? "text-destructive"
                                  : ratio >= 0.8
                                    ? "text-warning"
                                    : "text-muted-foreground",
                              )}
                            >
                              {usd(p.monthlySpendUsd ?? 0)}
                            </span>
                          )}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>

            {/* ── Attention list ────────────────────────────────────────── */}
            <AttentionList items={attention} />
          </div>

          {/* Governance roles never build — make the boundary explicit rather
              than leaving the absence of agent surfaces unexplained (§14.8). */}
          {isGovernanceTier && (
            <p className="border-line-soft text-muted-foreground rounded-lg border border-dashed px-4 py-3 text-[12.5px]">
              <Users className="mr-1.5 -mt-0.5 inline size-3.5" aria-hidden />
              You hold a governance role
              {variant === "business_unit" && soleUnit ? ` in ${soleUnit.displayName}` : ""}. You
              govern structure, limits and visibility — you do not run agents or approve delivery
              work. To unblock a stalled gate, reassign the agent&apos;s owner rather than
              approving it yourself.
            </p>
          )}

          {/* The mirror note for a Project Admin: their reach ends at their own
              projects, and the absent Business Unit controls are deliberate. */}
          {variant === "project_admin" && bindings.length > 0 && (
            <p className="border-line-soft text-muted-foreground rounded-lg border border-dashed px-4 py-3 text-[12.5px]">
              <ShieldCheck className="mr-1.5 -mt-0.5 inline size-3.5" aria-hidden />
              You administer{" "}
              {managedProjectIds.length === 1
                ? "one project"
                : `${managedProjectIds.length} projects`}
              . Budgets, connections and members are yours to set within them; the{" "}
              {BUSINESS_UNIT_LABEL.toLowerCase()} caps and approved model list above them belong to
              your {BUSINESS_UNIT_LABEL} Admin.
            </p>
          )}
        </>
      )}
    </div>
  );
}
