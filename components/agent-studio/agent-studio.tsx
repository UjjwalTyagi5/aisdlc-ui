"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, User } from "lucide-react";

import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { useRawSession } from "@/components/auth/session-provider";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { AGENT_DEFAULT_OWNER_ROLE } from "@/lib/governance";
import { ROLE_META } from "@/lib/roles";
import { getAgentProfilesSummary } from "@/lib/api/agent-profiles";
import { listProjects } from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import { useActiveWorkspace } from "@/hooks/use-workspaces";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import { cn } from "@/lib/utils";
import type { ProfileScope } from "@/lib/schemas/agent-profiles";

import { AgentEditor, type ScopeContext } from "./agent-editor";
import { AgentRail, AgentRailSkeleton } from "./agent-rail";
import { byPipelineOrder } from "./agents";

/** Bottom-to-top rank for escalation math — user(0) < project(1) < workspace(2) < org(3). */
const TIER_RANK: Record<ProfileScope, number> = { user: 0, project: 1, workspace: 2, org: 3 };

/**
 * Org → Business Unit → Project → Personal cascade (PRD addition — each tier
 * may publish its own default; an unset tier inherits the nearest ancestor's,
 * see AgentProfileSummaryEntry.inherited_from). This is a DRILL-DOWN, not a
 * flat set of sibling tabs: your own tier is home (Org Admin lands on
 * Organization; Business Unit Admin lands on their business unit; everyone
 * else lands on Personal), and you browse into descendants one level at a
 * time — Organization → pick a Business Unit → pick a Project within it.
 * Personal is reachable from wherever you've drilled to, since its
 * inheritance chain is whatever Org/BU/Project you're currently positioned
 * under (see AGENT_DEFAULT_OWNER_ROLE, lib/governance.ts).
 */
export function AgentStudio() {
  const session = useRawSession();
  const role = effectivePlatformRole(session);
  const {
    active: myWorkspace,
    workspaces,
    isLoading: wsLoading,
    isError: wsError,
    refetch: refetchWorkspaces,
  } = useActiveWorkspace();

  const [buId, setBuId] = React.useState<string | null>(null);
  const [projectId, setProjectId] = React.useState<string | null>(null);
  const [personal, setPersonal] = React.useState(false);
  const [selectedId, setSelectedId] = React.useState<string | null>(null);

  // Land on the viewer's own tier once we know who they are — org_admin
  // stays at the root (buId/projectId null = "org"); bu_admin lands on their
  // own business unit; everyone else lands on Personal.
  const homedRef = React.useRef(false);
  React.useEffect(() => {
    if (homedRef.current || wsLoading) return;
    if (role === "org_admin") {
      homedRef.current = true;
    } else if (role === "bu_admin") {
      if (myWorkspace) {
        setBuId(myWorkspace.id);
        homedRef.current = true;
      }
    } else {
      setPersonal(true);
      homedRef.current = true;
    }
  }, [role, myWorkspace, wsLoading]);

  const bu = workspaces.find((w) => w.id === buId) ?? null;

  const projectsQ = useQuery({
    queryKey: ["agent-studio", "projects", buId],
    queryFn: () => listProjects({ pageSize: 100 }),
    enabled: Boolean(buId),
    staleTime: 30_000,
  });
  const projectsInBu = React.useMemo(
    () => (projectsQ.data?.items ?? []).filter((p) => p.workspaceId === buId),
    [projectsQ.data, buId],
  );
  const project = projectsInBu.find((p) => p.id === projectId) ?? null;

  const tier: ProfileScope = personal ? "user" : projectId ? "project" : buId ? "workspace" : "org";

  const scopeId =
    tier === "org"
      ? null
      : tier === "workspace"
        ? buId
        : tier === "project"
          ? projectId
          : (session?.user.id ?? null);

  const chain = {
    workspaceId: buId,
    projectId,
    userId: personal ? (session?.user.id ?? null) : null,
  };

  /**
   * The governance tier has no PERSONAL agent instructions.
   *
   * Personal is the bottom of the cascade — the individual override someone
   * applies to their own runs. An Org or Business Unit Admin never runs an
   * agent (PRD §14.8), so a personal override for them would be instructions
   * that can never take effect: a tier of one, inherited by nobody, applied to
   * no run. Worse, it is a governance role editing agent behaviour outside the
   * cascade everyone else can see and inherit from, which is exactly the
   * visibility the tiered defaults exist to provide.
   *
   * They own a tier each — Organization and Business Unit — and those are
   * where their instructions belong.
   */
  const mayHavePersonalTier = role !== "org_admin" && role !== "bu_admin";

  const ownerRole = tier === "user" ? null : AGENT_DEFAULT_OWNER_ROLE[tier];
  const isOwner =
    tier === "user"
      ? mayHavePersonalTier && Boolean(session)
      : role !== null && role === ownerRole;
  const ownerRoleLabel = ownerRole ? ROLE_META[ownerRole].label : null;

  // Escalation only ever flows one rung up, from a person's OWN tier — never
  // sideways-skipping levels, and never downward. A contributor can propose
  // to Project (their tier + 1) but not straight to Business Unit or Org; a
  // BU Admin can propose to Org (their tier + 1) but has read-only access to
  // Project (below their own tier — that's the Project Admin's call, not
  // something to request). Org Admin's tier has no rung above it, so it
  // never proposes anywhere — only views, and edits its own tier directly.
  const homeRank = role === "org_admin" ? 3 : role === "bu_admin" ? 2 : role === "project_admin" ? 1 : 0;
  const viewedRank = TIER_RANK[tier];
  const canPropose = !isOwner && tier !== "user" && viewedRank === homeRank + 1;

  const scopeLabel =
    tier === "org"
      ? "Organization"
      : tier === "workspace"
        ? (bu?.displayName ?? BUSINESS_UNIT_LABEL)
        : tier === "project"
          ? (project?.name ?? "Project")
          : "Personal";

  const summaryQ = useQuery({
    queryKey: [
      ...qk.agentProfiles.summary(tier, scopeId),
      chain.workspaceId,
      chain.projectId,
      chain.userId,
    ],
    queryFn: () => getAgentProfilesSummary(tier, scopeId, chain),
  });

  const agents = React.useMemo(
    () => [...(summaryQ.data?.agents ?? [])].sort(byPipelineOrder),
    [summaryQ.data],
  );

  // Default selection = first agent in pipeline order, once data lands.
  React.useEffect(() => {
    const first = agents[0];
    if (!selectedId && first) setSelectedId(first.agent_id);
  }, [agents, selectedId]);

  const selected =
    agents.find((a) => a.agent_id === selectedId) ?? agents[0] ?? null;

  const scopeContext: ScopeContext = {
    scope: tier,
    scopeId,
    scopeLabel,
    chain,
    isOwner,
    canPropose,
    ownerRoleLabel,
    workspaceId: bu?.id,
    workspaceName: bu?.displayName,
    projectId: project?.id,
    projectName: project?.name,
  };

  const goToOrg = () => {
    setPersonal(false);
    setProjectId(null);
    setBuId(null);
  };
  const goToBu = () => {
    setPersonal(false);
    setProjectId(null);
  };
  const goToProject = () => setPersonal(false);

  return (
    <div className="w-full space-y-8 p-4 md:px-10 md:py-8">
      <Header scopeLabel={scopeLabel} />

      <div className="space-y-3">
        <Breadcrumb
          bu={bu}
          project={project}
          personal={personal}
          onOrg={goToOrg}
          onBu={goToBu}
          onProject={goToProject}
        />

        {!personal && !buId && (
          <BrowsePicker
            label="Browse a Business Unit"
            placeholder={wsLoading ? "Loading…" : "Choose a Business Unit…"}
            items={workspaces.map((w) => ({ id: w.id, name: w.displayName }))}
            disabled={wsLoading || workspaces.length === 0}
            onSelect={setBuId}
          />
        )}

        {!personal && buId && !projectId && (
          <BrowsePicker
            label="Browse a Project"
            placeholder={projectsQ.isLoading ? "Loading…" : "Choose a project…"}
            items={projectsInBu.map((p) => ({ id: p.id, name: p.name }))}
            disabled={projectsQ.isLoading || projectsInBu.length === 0}
            onSelect={setProjectId}
          />
        )}

        {/* Absent, not disabled, for the governance tier — see
            `mayHavePersonalTier`. A greyed-out link would imply a personal
            tier they could unlock, and there isn't one to unlock. */}
        {!personal && mayHavePersonalTier && (
          <button
            type="button"
            onClick={() => setPersonal(true)}
            className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1.5 text-xs underline-offset-2 hover:underline"
          >
            <User className="size-3.5" aria-hidden />
            View your personal instructions
          </button>
        )}
      </div>

      {wsError ? (
        <ApiErrorState title="Couldn't load business units" onRetry={refetchWorkspaces} />
      ) : summaryQ.isError ? (
        <ApiErrorState
          title="Couldn't load agents"
          error={
            summaryQ.error && "code" in summaryQ.error && "message" in summaryQ.error
              ? (summaryQ.error as { code: string; message: string; requestId?: string })
              : undefined
          }
          description={
            summaryQ.error instanceof Error ? summaryQ.error.message : undefined
          }
          onRetry={() => summaryQ.refetch()}
        />
      ) : summaryQ.isLoading || !selected ? (
        <TwoPaneSkeleton />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
          <div className="lg:sticky lg:top-6 lg:self-start">
            <AgentRail
              agents={agents}
              selectedId={selected.agent_id}
              onSelect={setSelectedId}
            />
          </div>
          <AgentEditor summary={selected} scopeContext={scopeContext} />
        </div>
      )}
    </div>
  );
}

function Breadcrumb({
  bu,
  project,
  personal,
  onOrg,
  onBu,
  onProject,
}: {
  bu: { displayName: string } | null;
  project: { name: string } | null;
  personal: boolean;
  onOrg: () => void;
  onBu: () => void;
  onProject: () => void;
}) {
  const isOrgCurrent = !bu && !personal;
  const isBuCurrent = Boolean(bu) && !project && !personal;
  const isProjectCurrent = Boolean(project) && !personal;

  return (
    <nav className="flex flex-wrap items-center gap-1 text-sm" aria-label="Agent Studio scope">
      <Crumb label="Organization" current={isOrgCurrent} onClick={onOrg} />
      {bu && (
        <>
          <ChevronRight className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
          <Crumb label={bu.displayName} current={isBuCurrent} onClick={onBu} />
        </>
      )}
      {project && (
        <>
          <ChevronRight className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
          <Crumb label={project.name} current={isProjectCurrent} onClick={onProject} />
        </>
      )}
      {personal && (
        <>
          <ChevronRight className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
          <Crumb label="Personal" current onClick={() => undefined} />
        </>
      )}
    </nav>
  );
}

function Crumb({
  label,
  current,
  onClick,
}: {
  label: string;
  current: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={current ? "true" : undefined}
      className={cn(
        "rounded-md px-2 py-1 font-medium transition-colors",
        current
          ? "bg-brand-bright text-white"
          : "text-muted-foreground hover:bg-accent/40 hover:text-foreground",
      )}
    >
      {label}
    </button>
  );
}

function BrowsePicker({
  label,
  placeholder,
  items,
  disabled,
  onSelect,
}: {
  label: string;
  placeholder: string;
  items: { id: string; name: string }[];
  disabled: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <label className="flex max-w-sm flex-col gap-1 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <select
        className="border-line-soft bg-background rounded-md border px-2.5 py-1.5 text-sm"
        value=""
        onChange={(e) => e.target.value && onSelect(e.target.value)}
        disabled={disabled}
      >
        <option value="" disabled>
          {items.length === 0 && !disabled ? "None available" : placeholder}
        </option>
        {items.map((it) => (
          <option key={it.id} value={it.id}>
            {it.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function Header({ scopeLabel }: { scopeLabel: string }) {
  return (
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
        {scopeLabel}
      </div>
      <h1 className="font-display text-[38px] leading-[1.02] font-bold tracking-[-0.03em]">
        Agent Studio
      </h1>
      <p className="text-muted-foreground mt-2 max-w-[640px] text-[14px]">
        Tune how each agent behaves. Instructions cascade Organization → Business
        Unit → Project → Personal — a tier with no override of its own inherits
        the nearest ancestor&apos;s. Your instructions are layered over the locked
        base prompt and versioned — publish to apply, roll back anytime.
      </p>
    </header>
  );
}

function TwoPaneSkeleton() {
  return (
    <div className="grid gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
      <AgentRailSkeleton />
      <Card className="space-y-5 p-6">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-9 w-56" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-10 w-64" />
      </Card>
    </div>
  );
}
