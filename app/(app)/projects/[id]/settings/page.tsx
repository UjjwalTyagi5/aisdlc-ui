"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Archive,
  CheckCircle2,
  ExternalLink,
  Loader2,
  Settings as SettingsIcon,
  ShieldAlert,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadingState } from "@/components/ui/loading-state";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

import { RequireRole } from "@/components/auth/require-role";
import { ProjectModelSelectionCard } from "@/components/app/project-model-selection-card";
import { ToolsStagePicker, type AccessModeMap, type StageMap } from "@/components/app/tools-stage-picker";
import { useRawSession } from "@/components/auth/session-provider";
import { hasPermission } from "@/lib/auth/permissions";
import { useCan } from "@/hooks/use-can";
import { listConnectors } from "@/lib/api/connectors";
import {
  archiveProject,
  getProject,
  restoreProject,
  updateProject,
} from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import type { AgentType, ProjectId } from "@/lib/schemas";

// Mirrors TECH_STACK_MVP.md §5 routing table. Static until Chunk 16 persists.
const AGENT_ROUTING: Array<{
  agent: AgentType;
  label: string;
  primary: string;
  fallback?: string;
  rationale: string;
}> = [
  {
    agent: "orchestrator",
    label: "Orchestrator",
    primary: "claude-sonnet-4-6",
    fallback: "gemini-2.5-flash",
    rationale: "Planning + tool delegation; Flash for routine re-plan.",
  },
  {
    agent: "requirements",
    label: "Requirements",
    primary: "claude-sonnet-4-6",
    fallback: "gemini-2.5-pro",
    rationale: "Gemini Pro for large-doc ingest, Sonnet for AC authoring.",
  },
  {
    agent: "design",
    label: "Design",
    primary: "claude-opus-4-7",
    fallback: "claude-sonnet-4-6",
    rationale: "Sonnet drafts, Opus finalizes HLD + OpenAPI + DDL.",
  },
  {
    agent: "development",
    label: "Development",
    primary: "claude-opus-4-7",
    fallback: "deepseek-v3.1",
    rationale: "Opus for real code; DeepSeek for mechanical edits.",
  },
  {
    agent: "review",
    label: "Code Review",
    primary: "claude-opus-4-7",
    fallback: "claude-haiku-4-5",
    rationale: "Opus for correctness + security; Haiku for style nits.",
  },
  {
    agent: "security",
    label: "Security",
    primary: "claude-sonnet-4-6",
    fallback: "claude-haiku-4-5",
    rationale: "SCA + OWASP SAST + secret scan triage and risk scoring.",
  },
  {
    agent: "testing",
    label: "Testing",
    primary: "claude-sonnet-4-6",
    fallback: "deepseek-v3.1",
    rationale: "Structural gen; DeepSeek for bulk coverage fill.",
  },
  {
    agent: "deployment",
    label: "Deployment",
    primary: "claude-opus-4-7",
    fallback: "claude-sonnet-4-6",
    rationale: "Opus for pipeline/IaC; Sonnet for trivial YAML edits.",
  },
  {
    agent: "documentation",
    label: "Documentation",
    primary: "claude-sonnet-4-6",
    fallback: "claude-haiku-4-5",
    rationale: "Compiles upstream artifacts into BRD, API refs, runbooks.",
  },
];

export default function ProjectSettingsPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id as ProjectId;
  const queryClient = useQueryClient();
  const canUpdate = useCan("project:update");
  // Budget edits are gated by the backend on workspace:manage (delivery lead + admin),
  // NOT the legacy project:update capability — mirror that here so the UI matches.
  const session = useRawSession();
  const canManageBudget = hasPermission(session, "workspace:manage");

  const projectQ = useQuery({
    queryKey: qk.projects.detail(projectId),
    queryFn: () => getProject(projectId),
  });

  // Scoped to the project's Business Unit: a project may only wire up what it
  // inherits — org-wide connectors plus its own unit's (PRD §34.3).
  const projectWorkspaceId = projectQ.data?.workspaceId ?? null;
  const connectorsQ = useQuery({
    queryKey: qk.connectors.list(projectWorkspaceId),
    queryFn: () => listConnectors(projectWorkspaceId),
    enabled: projectQ.isSuccess,
  });

  const [form, setForm] = React.useState<{ name: string; description: string } | null>(null);
  React.useEffect(() => {
    if (projectQ.data) {
      setForm({
        name: projectQ.data.name,
        description: projectQ.data.description ?? "",
      });
    }
  }, [projectQ.data]);

  const updateMutation = useMutation({
    mutationFn: (patch: { name?: string; description?: string }) =>
      updateProject(projectId, patch),
    onSuccess: () => {
      toast.success("Project updated");
      queryClient.invalidateQueries({ queryKey: qk.projects.all() });
    },
    onError: (err) =>
      toast.error("Couldn't update", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  // Tools per stage (MCP servers + connectors, each with a read/write/both
  // access mode) — editable after creation.
  const [mcpSel, setMcpSel] = React.useState<StageMap>({});
  const [connSel, setConnSel] = React.useState<StageMap>({});
  const [accessModeSel, setAccessModeSel] = React.useState<AccessModeMap>({});
  React.useEffect(() => {
    if (projectQ.data) {
      setMcpSel(projectQ.data.mcpServers ?? {});
      setConnSel(projectQ.data.connectors ?? {});
      setAccessModeSel(projectQ.data.toolAccessModes ?? {});
    }
  }, [projectQ.data]);

  const clean = (m: StageMap) =>
    Object.fromEntries(Object.entries(m).filter(([, ids]) => ids.length > 0));

  const toolsMutation = useMutation({
    mutationFn: () =>
      updateProject(projectId, {
        mcp_servers: clean(mcpSel),
        connectors: clean(connSel),
        tool_access_modes: accessModeSel,
      }),
    onSuccess: () => {
      toast.success("Tools updated");
      queryClient.invalidateQueries({ queryKey: qk.projects.all() });
    },
    onError: (err) =>
      toast.error("Couldn't update", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const archiveMutation = useMutation({
    mutationFn: (archive: boolean) =>
      archive ? archiveProject(projectId) : restoreProject(projectId),
    onSuccess: (p, requestedArchive) => {
      if (requestedArchive && !p.archived) {
        // A BU Admin's archive request isn't applied immediately — it opens
        // a governance approval routed to the Org Admin instead.
        toast.info("Sent for approval", {
          description: "Your Org Admin needs to approve archiving this project.",
        });
      } else {
        toast.success(p.archived ? "Archived" : "Restored");
      }
      queryClient.invalidateQueries({ queryKey: qk.projects.all() });
      queryClient.invalidateQueries({ queryKey: qk.projects.detail(projectId) });
    },
  });

  const budgetMutation = useMutation({
    mutationFn: (monthlyBudgetUsd: number | null) =>
      updateProject(projectId, { monthlyBudgetUsd }),
    onSuccess: () => {
      toast.success("Budget updated");
      queryClient.invalidateQueries({ queryKey: qk.projects.detail(projectId) });
      queryClient.invalidateQueries({ queryKey: qk.projects.all() });
    },
    onError: (err) =>
      toast.error("Couldn't update budget", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  if (projectQ.isLoading) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
        <LoadingState variant="card" />
      </div>
    );
  }
  if (projectQ.isError || !projectQ.data) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
        <ErrorState
          title="Project not found"
          description={
            projectQ.error instanceof Error ? projectQ.error.message : "Unknown error."
          }
          onRetry={() => projectQ.refetch()}
        />
      </div>
    );
  }

  const project = projectQ.data;
  const installedConnectors = connectorsQ.data?.filter((c) => c.installed) ?? [];
  const dirty =
    form !== null &&
    (form.name !== project.name ||
      (form.description || "") !== (project.description ?? ""));

  return (
    <div className="max-w-4xl space-y-5 p-4 md:px-10 md:py-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Project settings</h1>
        <p className="text-muted-foreground text-sm">
          {project.name} · <span className="font-mono text-xs">{project.slug}</span>
        </p>
      </header>

      <Tabs defaultValue="general">
        <TabsList>
          <TabsTrigger value="general">General</TabsTrigger>
          <TabsTrigger value="connectors">Connectors</TabsTrigger>
          <TabsTrigger value="policies">Policies</TabsTrigger>
          <TabsTrigger value="model">Model</TabsTrigger>
        </TabsList>

        {/* ─── General ─── */}
        <TabsContent value="general" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">General</CardTitle>
              <CardDescription>Basic project metadata.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {form && (
                <>
                  <div className="space-y-1.5">
                    <Label htmlFor="project-name">Name</Label>
                    <Input
                      id="project-name"
                      value={form.name}
                      onChange={(e) => setForm({ ...form, name: e.target.value })}
                      disabled={!canUpdate}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="project-description">Description</Label>
                    <Textarea
                      id="project-description"
                      rows={3}
                      value={form.description}
                      onChange={(e) => setForm({ ...form, description: e.target.value })}
                      disabled={!canUpdate}
                    />
                  </div>
                  <div className="flex justify-end gap-2">
                    <Button
                      variant="ghost"
                      disabled={!dirty || updateMutation.isPending}
                      onClick={() =>
                        setForm({
                          name: project.name,
                          description: project.description ?? "",
                        })
                      }
                    >
                      Reset
                    </Button>
                    <RequireRole
                      capability="project:update"
                      fallback={<Button disabled>Save</Button>}
                    >
                      <Button
                        disabled={!dirty || updateMutation.isPending}
                        onClick={() => updateMutation.mutate(form!)}
                        aria-busy={updateMutation.isPending}
                      >
                        {updateMutation.isPending && (
                          <Loader2 className="size-4 animate-spin" aria-hidden />
                        )}
                        Save
                      </Button>
                    </RequireRole>
                  </div>
                </>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Tools per stage</CardTitle>
              <CardDescription>
                Choose which connectors and MCP servers each agent stage can use. Applies to new
                runs.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <ToolsStagePicker
                mcpValue={mcpSel}
                onMcpChange={setMcpSel}
                connectorValue={connSel}
                onConnectorChange={setConnSel}
                accessModeValue={accessModeSel}
                onAccessModeChange={setAccessModeSel}
                disabled={!canUpdate}
                workspaceId={projectWorkspaceId}
              />
              <div className="flex justify-end gap-2">
                <Button
                  variant="ghost"
                  disabled={toolsMutation.isPending}
                  onClick={() => {
                    setMcpSel(project.mcpServers ?? {});
                    setConnSel(project.connectors ?? {});
                    setAccessModeSel(project.toolAccessModes ?? {});
                  }}
                >
                  Reset
                </Button>
                <RequireRole capability="project:update" fallback={<Button disabled>Save</Button>}>
                  <Button
                    disabled={toolsMutation.isPending}
                    aria-busy={toolsMutation.isPending}
                    onClick={() => toolsMutation.mutate()}
                  >
                    {toolsMutation.isPending && (
                      <Loader2 className="size-4 animate-spin" aria-hidden />
                    )}
                    Save
                  </Button>
                </RequireRole>
              </div>
            </CardContent>
          </Card>

          <ProjectBudgetCard
            spend={project.monthlySpendUsd ?? 0}
            budget={project.monthlyBudgetUsd ?? null}
            canManage={canManageBudget}
            saving={budgetMutation.isPending}
            onSave={(v) => budgetMutation.mutate(v)}
          />

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Archive</CardTitle>
              <CardDescription>
                Archived projects are read-only. Agents won&apos;t run. Restore any time.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-sm">
                <Archive className="text-muted-foreground size-4" aria-hidden />
                {project.archived ? (
                  <Badge variant="outline" className="text-muted-foreground">
                    Archived
                  </Badge>
                ) : (
                  <Badge variant="success">
                    <CheckCircle2 className="size-3" aria-hidden />
                    Active
                  </Badge>
                )}
              </div>
              <RequireRole capability="project:update">
                <Button
                  variant="outline"
                  onClick={() => archiveMutation.mutate(!project.archived)}
                  disabled={archiveMutation.isPending}
                >
                  {project.archived ? "Restore project" : "Archive project"}
                </Button>
              </RequireRole>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ─── Connectors ─── */}
        <TabsContent value="connectors" className="space-y-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-2">
              <div>
                <CardTitle className="text-base">Connectors</CardTitle>
                <CardDescription>
                  Tools this project can read from and write to.
                </CardDescription>
              </div>
              <Button variant="outline" size="sm" asChild>
                <Link href="/integrations">
                  Manage
                  <ExternalLink className="size-3.5" aria-hidden />
                </Link>
              </Button>
            </CardHeader>
            <CardContent>
              {connectorsQ.isLoading ? (
                <LoadingState variant="list" rows={3} />
              ) : installedConnectors.length === 0 ? (
                <p className="text-muted-foreground text-sm">
                  No connectors installed.{" "}
                  <Link href="/integrations" className="underline">
                    Install one
                  </Link>{" "}
                  to run agents against your real tools.
                </p>
              ) : (
                <ul className="divide-y">
                  {installedConnectors.map((c) => (
                    <li key={c.id} className="flex items-center justify-between gap-2 py-2.5">
                      <div className="flex min-w-0 flex-col">
                        <span className="text-sm font-medium">{c.name}</span>
                        {c.account && (
                          <span className="text-muted-foreground truncate font-mono text-xs">
                            {c.account}
                          </span>
                        )}
                      </div>
                      <span className="text-muted-foreground text-xs capitalize">
                        {c.health}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ─── Policies ─── */}
        <TabsContent value="policies" className="space-y-4">
          <PoliciesCard />
        </TabsContent>

        {/* ─── Model ─── */}
        <TabsContent value="model" className="space-y-4">
          {/* The cascade's last step: org allow-list → BU allow-list → this. */}
          <ProjectModelSelectionCard
            projectId={projectId}
            canManage={hasPermission(session, "model:manage")}
          />

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Model routing</CardTitle>
              <CardDescription>
                Per-agent model from <code className="bg-muted rounded px-1">TECH_STACK_MVP.md</code>{" "}
                §5. Persistence ships in Chunk 16.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <ul className="divide-y rounded-md border">
                {AGENT_ROUTING.map((r) => (
                  <li key={r.agent} className="flex flex-col gap-1 px-3 py-2.5 text-sm">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">{r.label}</span>
                      <div className="flex items-center gap-2 font-mono text-[11px]">
                        <Badge variant="secondary">{r.primary}</Badge>
                        {r.fallback && (
                          <Badge variant="outline" className="text-muted-foreground">
                            fallback · {r.fallback}
                          </Badge>
                        )}
                      </div>
                    </div>
                    <p className="text-muted-foreground text-xs">{r.rationale}</p>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ───────── Monthly budget card (persisted — migration 0032) ─────────

function fmtUsd(n: number): string {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
}

function ProjectBudgetCard({
  spend,
  budget,
  canManage,
  saving,
  onSave,
}: {
  spend: number;
  budget: number | null;
  canManage: boolean;
  saving: boolean;
  onSave: (v: number | null) => void;
}) {
  const [value, setValue] = React.useState<string>(budget !== null ? String(budget) : "");
  React.useEffect(() => {
    setValue(budget !== null ? String(budget) : "");
  }, [budget]);

  const pct = budget && budget > 0 ? Math.min(100, (spend / budget) * 100) : 0;
  const over = budget !== null && budget > 0 && spend >= budget;
  const warn = budget !== null && budget > 0 && !over && spend >= 0.8 * budget;
  const barTone = over ? "bg-destructive" : warn ? "bg-warning" : "bg-primary";

  const commit = () => {
    const trimmed = value.trim();
    const parsed = trimmed === "" ? 0 : Number(trimmed);
    if (Number.isNaN(parsed) || parsed < 0) {
      toast.error("Enter a valid non-negative amount");
      return;
    }
    onSave(parsed === 0 ? null : parsed);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Monthly budget</CardTitle>
        <CardDescription>
          Calendar-month cost cap for this project. New runs are blocked once spend reaches the cap
          (org and workspace caps also apply). Leave 0 for no project cap.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <div className="flex items-baseline justify-between font-mono text-[12px]">
            <span className="font-semibold">{fmtUsd(spend)} used this month</span>
            <span className="text-muted-foreground">
              {budget !== null && budget > 0 ? `of ${fmtUsd(budget)}` : "No project cap"}
            </span>
          </div>
          {budget !== null && budget > 0 && (
            <div className="bg-muted h-2 overflow-hidden rounded-full">
              <div className={`h-full rounded-full transition-all ${barTone}`} style={{ width: `${pct}%` }} />
            </div>
          )}
          {over && (
            <p className="text-destructive text-xs">
              Over budget — new runs are blocked until the cap is raised or the month resets.
            </p>
          )}
        </div>

        <div className="flex items-center gap-3">
          <span className="text-muted-foreground font-mono text-sm">$</span>
          <Input
            type="number"
            min={0}
            step={1}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            disabled={!canManage}
            placeholder="0 = no cap"
            className="h-8 w-32"
          />
          <span className="text-muted-foreground text-xs">per month</span>
          <div className="flex-1" />
          <Button disabled={!canManage || saving} aria-busy={saving} onClick={commit}>
            {saving && <Loader2 className="size-4 animate-spin" aria-hidden />}
            Save budget
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ───────── Policies card (local state only for MVP) ─────────

function PoliciesCard() {
  const [policy, setPolicy] = React.useState({
    approvalPhases: {
      requirements: true,
      design: true,
      development: true,
      review: true,
      testing: true,
      deployment: true,
    },
    branchProtectionEnabled: true,
    requiredReviewers: 1,
    budgetCapUsd: 10,
  });

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Approval gates</CardTitle>
          <CardDescription>
            Which phases pause for a human decision. Production deploy is always gated
            and cannot be disabled.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {(
            Object.keys(policy.approvalPhases) as Array<keyof typeof policy.approvalPhases>
          ).map((phase) => {
            const locked = phase === "deployment";
            return (
              <div key={phase} className="flex items-center justify-between gap-2">
                <Label htmlFor={`gate-${phase}`} className="cursor-pointer capitalize">
                  {phase}
                  {locked && (
                    <span className="text-destructive ml-2 inline-flex items-center gap-1 text-[10px] font-mono uppercase">
                      <ShieldAlert className="size-3" aria-hidden />
                      locked on
                    </span>
                  )}
                </Label>
                <Switch
                  id={`gate-${phase}`}
                  checked={policy.approvalPhases[phase]}
                  onCheckedChange={(v) => {
                    if (locked) return;
                    setPolicy((p) => ({
                      ...p,
                      approvalPhases: { ...p.approvalPhases, [phase]: v },
                    }));
                  }}
                  disabled={locked}
                />
              </div>
            );
          })}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Branch protection</CardTitle>
          <CardDescription>
            Governs the Dev agent&apos;s ability to merge PRs. Propagated to the SCM
            connector at save time.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor="bp-enabled">Require reviewers before merge</Label>
            <Switch
              id="bp-enabled"
              checked={policy.branchProtectionEnabled}
              onCheckedChange={(v) =>
                setPolicy((p) => ({ ...p, branchProtectionEnabled: v }))
              }
            />
          </div>
          <Separator />
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor="bp-count">Required reviewers</Label>
            <Input
              id="bp-count"
              type="number"
              min={0}
              max={10}
              disabled={!policy.branchProtectionEnabled}
              value={policy.requiredReviewers}
              onChange={(e) =>
                setPolicy((p) => ({
                  ...p,
                  requiredReviewers: Math.max(0, Math.min(10, Number(e.target.value))),
                }))
              }
              className="h-8 w-20"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Budget cap</CardTitle>
          <CardDescription>
            Per-run USD limit. Runs over the cap pause and require budget override.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex items-center gap-3">
          <SettingsIcon className="text-muted-foreground size-4" aria-hidden />
          <Label htmlFor="budget" className="flex-1">
            Stop at
          </Label>
          <span className="text-muted-foreground font-mono text-sm">$</span>
          <Input
            id="budget"
            type="number"
            min={1}
            step={1}
            value={policy.budgetCapUsd}
            onChange={(e) =>
              setPolicy((p) => ({
                ...p,
                budgetCapUsd: Math.max(1, Number(e.target.value)),
              }))
            }
            className="h-8 w-28"
          />
          <span className="text-muted-foreground text-xs">per run</span>
        </CardContent>
      </Card>

      <p className="text-muted-foreground text-xs">
        Changes save locally in Chunk 14. Persistence to the policy endpoint ships in
        Chunk 16.
      </p>
    </>
  );
}
