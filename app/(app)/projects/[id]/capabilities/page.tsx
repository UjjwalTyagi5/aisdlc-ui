"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ChevronDown,
  ChevronRight,
  Cpu,
  Lock,
  Loader2,
  Plug,
  Puzzle,
  ShieldPlus,
  Sparkles,
  Trash2,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { Skeleton } from "@/components/ui/skeleton";
import { useRawSession } from "@/components/auth/session-provider";
import { can } from "@/lib/auth/capabilities";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import {
  getProjectCapabilities,
  setAgentCuratedDisabled,
  listAgentAccessOverrides,
  setAgentAccessOverride,
  removeAgentAccessOverride,
} from "@/lib/api/capabilities";
import { updateProject } from "@/lib/api/projects";
import { listCustomRoles } from "@/lib/api/roles";
import { qk } from "@/lib/api/query-keys";
import { PHASE_LABEL } from "@/lib/agents";
import { ROLE_META, ROLE_ORDER } from "@/lib/roles";
import type {
  AgentCapability,
  AvailableByo,
} from "@/lib/schemas/capabilities";
import type { InvolvementLevel } from "@/lib/schemas/agent-access";
import type { Phase, ProjectId } from "@/lib/schemas";

/**
 * Canonical SDLC pipeline order by backend agent_id. Mirrors lib/agents PHASE_ORDER
 * (the `review` phase = the `code_review` agent here). The backend capability API
 * returns agents in registry order, which is NOT pipeline order — sort to match the
 * sidebar/pipeline everywhere else.
 */
const AGENT_ORDER = [
  "requirements",
  "design",
  "development",
  "code_review",
  "security",
  "testing",
  "deployment",
  "documentation",
  "discovery",
  "strategy",
  "migration_mapping",
  "validation",
  "data_engineering",
] as const;

const agentRank = (id: string) => {
  const i = AGENT_ORDER.indexOf(id as (typeof AGENT_ORDER)[number]);
  return i === -1 ? AGENT_ORDER.length : i;
};

/** agent_id → Phase — the inverse of tools-stage-picker.tsx's
 *  PHASE_TO_AGENT_ID; every phase equals its agent id except `review`, whose
 *  agent id is `code_review`. */
function agentIdToPhase(agentId: string): Phase {
  return (agentId === "code_review" ? "review" : agentId) as Phase;
}

const INVOLVEMENT_LABEL: Record<InvolvementLevel, string> = {
  owner: "Owner (uses + approves)",
  primary: "Primary (uses + approves)",
  build: "Build (hands-on)",
  requests: "Requests only",
  use: "Use only",
  none: "No access",
};

// Roles a Project Admin can grant/restrict agent access for, at project
// level — every project-scoped role except Project Admin itself (already
// owner on every agent) and the generic "custom" placeholder (a specific
// custom role instance is targeted by its id instead).
const OVERRIDABLE_BUILTIN_ROLES = ROLE_ORDER.filter(
  (r) => ROLE_META[r].scope === "project" && r !== "project_admin" && r !== "custom",
);

export default function CapabilitiesPage() {
  const params = useParams<{ id: string }>();
  const id = params.id as ProjectId;
  const session = useRawSession();
  const canManage = session ? can(session.role, "project:update") : false;
  const isProjectAdmin = effectivePlatformRole(session) === "project_admin";

  const capsQ = useQuery({
    queryKey: qk.capabilities.forProject(id),
    queryFn: () => getProjectCapabilities(id),
  });

  if (capsQ.isLoading) {
    return (
      <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
        <Skeleton className="h-8 w-72" />
        <LoadingState variant="card" />
      </div>
    );
  }

  if (capsQ.isError || !capsQ.data) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
        <ErrorState
          title="Couldn't load capabilities"
          description={
            capsQ.error instanceof Error
              ? capsQ.error.message
              : "The project's agent capabilities are unavailable."
          }
          onRetry={() => capsQ.refetch()}
        />
      </div>
    );
  }

  const data = capsQ.data;
  const agents = [...data.agents].sort(
    (a, b) => agentRank(a.agent_id) - agentRank(b.agent_id),
  );

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Agents &amp; Capabilities</h1>
        <p className="text-muted-foreground max-w-2xl text-sm">
          See each agent&apos;s toolkit and tune it for this project. Native tools are
          built in. Curated tools can be turned on or off. Your own MCP servers
          (connected on{" "}
          <Link href="/integrations" className="text-brand-bright underline">
            Integrations
          </Link>
          ) can be assigned per agent.
        </p>
        {!canManage && (
          <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
            <Lock className="size-3" aria-hidden />
            You have read-only access. Ask an admin to assign tools or toggle curated
            ones.
          </p>
        )}
      </header>

      {/* The connectors this project runs on used to be restated here. They now
          have a screen of their own — Integrations — which also carries the
          project's credentials against them. Two lists of the same tools was
          one more than could stay in agreement. */}

      <RoleAccessOverridesCard
        projectId={id}
        projectAgentIds={agents.map((a) => a.agent_id)}
        canManage={isProjectAdmin}
      />

      <ul className="space-y-3">
        {agents.map((agent) => (
          <li key={agent.agent_id}>
            <AgentRow
              projectId={id}
              agent={agent}
              availableByo={data.available_byo}
              allAgents={agents}
              canManage={canManage}
            />
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Grant (or restrict) any project-scoped role's access to any of this
 * project's agents — on top of that role's global default. E.g. Architect
 * normally only owns Design/Development/Code Review; a Project Admin can
 * grant it `build` on Development too, but only for this one project.
 */
function RoleAccessOverridesCard({
  projectId,
  projectAgentIds,
  canManage,
}: {
  projectId: ProjectId;
  projectAgentIds: string[];
  canManage: boolean;
}) {
  const queryClient = useQueryClient();
  const phases = projectAgentIds.map(agentIdToPhase);

  const overridesQ = useQuery({
    queryKey: qk.agentAccessOverrides.forProject(projectId),
    queryFn: () => listAgentAccessOverrides(projectId),
  });
  const customRolesQ = useQuery({
    queryKey: ["custom-roles", "list"],
    queryFn: () => listCustomRoles(),
  });

  const roleOptions: { value: string; label: string }[] = [
    ...OVERRIDABLE_BUILTIN_ROLES.map((r) => ({ value: r, label: ROLE_META[r].label })),
    ...(customRolesQ.data ?? []).map((r) => ({ value: r.id, label: r.name })),
  ];
  const roleLabel = (value: string) =>
    roleOptions.find((r) => r.value === value)?.label ?? value;

  const [role, setRole] = React.useState<string>("");
  const [phase, setPhase] = React.useState<Phase | "">("");
  const [involvement, setInvolvement] = React.useState<InvolvementLevel>("build");

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: qk.agentAccessOverrides.forProject(projectId) });

  const setMutation = useMutation({
    mutationFn: () =>
      setAgentAccessOverride(projectId, { role, phase: phase as Phase, involvement }),
    onSuccess: () => {
      toast.success("Access granted for this project");
      setRole("");
      setPhase("");
      invalidate();
    },
    onError: (e) =>
      toast.error("Couldn't grant access", {
        description: e instanceof Error ? e.message : undefined,
      }),
  });

  const removeMutation = useMutation({
    mutationFn: (vars: { role: string; phase: Phase }) =>
      removeAgentAccessOverride(projectId, vars.role, vars.phase),
    onSuccess: () => {
      toast.success("Reverted to default access");
      invalidate();
    },
    onError: (e) =>
      toast.error("Couldn't remove override", {
        description: e instanceof Error ? e.message : undefined,
      }),
  });

  if (!canManage && (overridesQ.data ?? []).length === 0) return null;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <ShieldPlus className="size-4" aria-hidden />
          Role access overrides
        </CardTitle>
        <CardDescription>
          Grant a role extra agent access for just this project — e.g. let Architect build on
          Development here, on top of what it can do by default everywhere else.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {canManage && (
          <div className="flex flex-wrap items-center gap-2">
            <Select value={role} onValueChange={setRole}>
              <SelectTrigger className="w-48">
                <SelectValue placeholder="Role" />
              </SelectTrigger>
              <SelectContent>
                {roleOptions.map((r) => (
                  <SelectItem key={r.value} value={r.value}>
                    {r.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={phase} onValueChange={(v) => setPhase(v as Phase)}>
              <SelectTrigger className="w-48">
                <SelectValue placeholder="Agent" />
              </SelectTrigger>
              <SelectContent>
                {phases.map((p) => (
                  <SelectItem key={p} value={p}>
                    {PHASE_LABEL[p]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={involvement} onValueChange={(v) => setInvolvement(v as InvolvementLevel)}>
              <SelectTrigger className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(INVOLVEMENT_LABEL) as InvolvementLevel[]).map((level) => (
                  <SelectItem key={level} value={level}>
                    {INVOLVEMENT_LABEL[level]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              size="sm"
              disabled={!role || !phase || setMutation.isPending}
              aria-busy={setMutation.isPending}
              onClick={() => setMutation.mutate()}
            >
              {setMutation.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Grant access
            </Button>
          </div>
        )}

        {overridesQ.isLoading ? (
          <p className="text-muted-foreground text-sm">Loading…</p>
        ) : (overridesQ.data ?? []).length === 0 ? (
          <p className="text-muted-foreground text-sm">No overrides for this project yet.</p>
        ) : (
          <ul className="divide-border divide-y rounded-md border">
            {(overridesQ.data ?? []).map((o) => (
              <li key={o.id} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
                <span className="min-w-0 truncate">
                  <span className="font-medium">{roleLabel(o.role)}</span>
                  {" → "}
                  {PHASE_LABEL[o.phase]}:{" "}
                  <span className="text-muted-foreground">{INVOLVEMENT_LABEL[o.involvement]}</span>
                </span>
                {canManage && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7 shrink-0"
                    aria-label={`Remove override for ${roleLabel(o.role)} on ${PHASE_LABEL[o.phase]}`}
                    onClick={() => removeMutation.mutate({ role: o.role, phase: o.phase })}
                  >
                    <Trash2 className="size-3.5" aria-hidden />
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function AgentRow({
  projectId,
  agent,
  availableByo,
  allAgents,
  canManage,
}: {
  projectId: ProjectId;
  agent: AgentCapability;
  availableByo: AvailableByo[];
  allAgents: AgentCapability[];
  canManage: boolean;
}) {
  const [open, setOpen] = React.useState(false);
  const queryClient = useQueryClient();

  const curatedEnabled = agent.curated.filter((c) => c.enabled).length;
  const assignedIds = new Set(agent.assigned_byo.map((b) => b.id));

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: qk.capabilities.forProject(projectId) });

  // Curated on/off → write the agent's full disabled list.
  const curatedMutation = useMutation({
    mutationFn: (disabled: string[]) =>
      setAgentCuratedDisabled(projectId, agent.agent_id, disabled),
    onSuccess: () => {
      invalidate();
    },
    onError: (e) =>
      toast.error("Couldn't update curated tools", {
        description: e instanceof Error ? e.message : undefined,
      }),
  });

  const onToggleCurated = (key: string, enabled: boolean) => {
    const disabled = new Set(agent.curated.filter((c) => !c.enabled).map((c) => c.key));
    if (enabled) disabled.delete(key);
    else disabled.add(key);
    curatedMutation.mutate([...disabled]);
  };

  // BYO assignment → patch the whole project map, changing only this agent's list.
  const assignMutation = useMutation({
    mutationFn: (nextIds: string[]) => {
      const map: Record<string, string[]> = {};
      for (const a of allAgents) {
        const ids = a.agent_id === agent.agent_id ? nextIds : a.assigned_byo.map((b) => b.id);
        if (ids.length > 0) map[a.agent_id] = ids;
      }
      return updateProject(projectId, { mcp_servers: map });
    },
    onSuccess: () => {
      invalidate();
      queryClient.invalidateQueries({ queryKey: qk.projects.detail(projectId) });
    },
    onError: (e) =>
      toast.error("Couldn't update assigned servers", {
        description: e instanceof Error ? e.message : undefined,
      }),
  });

  const onToggleByo = (serverId: string, checked: boolean) => {
    const next = new Set(assignedIds);
    if (checked) next.add(serverId);
    else next.delete(serverId);
    assignMutation.mutate([...next]);
  };

  const busy = curatedMutation.isPending || assignMutation.isPending;

  return (
    <Card className={cn(open && "ring-line-soft ring-1")}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="hover:bg-accent/40 flex w-full items-center gap-3 rounded-t-lg px-4 py-3 text-left transition-colors"
      >
        {open ? (
          <ChevronDown className="text-muted-foreground size-4 shrink-0" aria-hidden />
        ) : (
          <ChevronRight className="text-muted-foreground size-4 shrink-0" aria-hidden />
        )}
        <span className="flex-1 font-medium">{agent.name}</span>
        <span className="flex flex-wrap items-center gap-1.5">
          <Badge variant="outline" className="text-[10px]">
            native {agent.native.length}
          </Badge>
          <Badge variant="outline" className="text-[10px]">
            curated {curatedEnabled}/{agent.curated.length}
          </Badge>
          <Badge variant="secondary" className="text-[10px]">
            BYO {agent.assigned_byo.length}
          </Badge>
        </span>
      </button>

      {open && (
        <CardContent className="border-line-soft space-y-5 border-t p-4">
          {/* Native */}
          <Section icon={Cpu} title="Native tools" hint="Built in · always on">
            {agent.native.length === 0 ? (
              <Empty>No native tools.</Empty>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {agent.native.map((n) => (
                  <Badge
                    key={n.tool}
                    variant="outline"
                    className="font-mono text-[10px] font-normal"
                    title={n.capability}
                  >
                    {n.tool}
                  </Badge>
                ))}
              </div>
            )}
          </Section>

          {/* Curated */}
          <Section icon={Sparkles} title="Curated tools" hint="Platform-shipped · toggle per agent">
            {agent.curated.length === 0 ? (
              <Empty>No curated tools for this agent.</Empty>
            ) : (
              <ul className="divide-line-soft divide-y">
                {agent.curated.map((c) => (
                  <li key={c.key} className="flex items-center gap-3 py-2">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{c.display_name}</p>
                      <p className="text-muted-foreground font-mono text-[10px]">
                        {c.capability}
                      </p>
                    </div>
                    <Switch
                      checked={c.enabled}
                      disabled={!canManage || busy}
                      onCheckedChange={(v) => onToggleCurated(c.key, v)}
                      aria-label={`${c.enabled ? "Disable" : "Enable"} ${c.display_name}`}
                    />
                  </li>
                ))}
              </ul>
            )}
          </Section>

          {/* BYO */}
          <Section icon={Puzzle} title="Your MCP servers" hint="Assign connected servers to this agent">
            {availableByo.length === 0 ? (
              <div className="flex flex-col items-start gap-2">
                <Empty>No MCP servers connected.</Empty>
                <Link
                  href="/integrations"
                  className="text-brand-bright inline-flex items-center gap-1.5 text-xs underline"
                >
                  <Plug className="size-3" aria-hidden />
                  Connect one on Integrations
                </Link>
              </div>
            ) : (
              <ul className="space-y-1.5">
                {availableByo.map((s) => {
                  const checked = assignedIds.has(s.id);
                  return (
                    <li key={s.id} className="flex items-center gap-3">
                      <Checkbox
                        id={`${agent.agent_id}:${s.id}`}
                        checked={checked}
                        disabled={!canManage || busy}
                        onCheckedChange={(v) => onToggleByo(s.id, v === true)}
                      />
                      <label
                        htmlFor={`${agent.agent_id}:${s.id}`}
                        className="flex min-w-0 flex-1 items-center gap-2 text-sm"
                      >
                        <span className="truncate font-medium">{s.server_name}</span>
                        <Badge variant="outline" className="text-[10px]">
                          {s.transport}
                        </Badge>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </Section>
        </CardContent>
      )}
    </Card>
  );
}

function Section({
  icon: Icon,
  title,
  hint,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <div className="flex items-center gap-2">
        <Icon className="text-muted-foreground size-3.5" aria-hidden />
        <h3 className="text-xs font-semibold uppercase tracking-wider">{title}</h3>
        {hint && <span className="text-muted-foreground text-[10px]">· {hint}</span>}
      </div>
      {children}
    </section>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-muted-foreground text-xs">{children}</p>;
}
