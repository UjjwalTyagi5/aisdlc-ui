"use client";

import * as React from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { GitBranch, GitPullRequest, Loader2, Rocket } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { LoadingState } from "@/components/ui/loading-state";
import { listAdoProjects, listAdoRepos, listAdoBranches } from "@/lib/api/dev-workspace";
import { listDeployConnectors, listOpenPrs, prepareDeploy } from "@/lib/api/deployment";
import { qk } from "@/lib/api/query-keys";
import type { PrepareDeployResult } from "@/lib/schemas/deployment";
import type { ProjectId } from "@/lib/schemas";

type Mode = "branch" | "pr";

export interface DeployTargetDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: ProjectId;
  onPrepared: (result: PrepareDeployResult) => void;
}

export function DeployTargetDialog({ open, onOpenChange, projectId, onPrepared }: DeployTargetDialogProps) {
  const [mode, setMode] = React.useState<Mode>("branch");
  const [project, setProject] = React.useState<string | null>(null);
  const [repo, setRepo] = React.useState<string | null>(null);
  const [branch, setBranch] = React.useState<string | null>(null);
  const [prId, setPrId] = React.useState<string | null>(null);
  const [env, setEnv] = React.useState("staging");
  const [deployVia, setDeployVia] = React.useState("");
  const [registry, setRegistry] = React.useState("");
  const [imageName, setImageName] = React.useState("");
  const [namespace, setNamespace] = React.useState("");

  React.useEffect(() => {
    if (!open) {
      setMode("branch"); setProject(null); setRepo(null); setBranch(null); setPrId(null);
      setEnv("staging"); setDeployVia(""); setRegistry(""); setImageName(""); setNamespace("");
    }
  }, [open]);

  const projectsQ = useQuery({ queryKey: qk.devWorkspace.adoProjects(projectId), queryFn: () => listAdoProjects(projectId), enabled: open, staleTime: 30_000 });
  const reposQ = useQuery({ queryKey: qk.devWorkspace.adoRepos(projectId, project ?? ""), queryFn: () => listAdoRepos(projectId, project!), enabled: open && !!project });
  const branchesQ = useQuery({ queryKey: qk.devWorkspace.adoBranches(projectId, project ?? "", repo ?? ""), queryFn: () => listAdoBranches(projectId, project!, repo!), enabled: open && mode === "branch" && !!project && !!repo });
  const prsQ = useQuery({ queryKey: qk.deployment.prs(projectId, project ?? "", repo ?? ""), queryFn: () => listOpenPrs(projectId, project!, repo!), enabled: open && mode === "pr" && !!project && !!repo });
  const connectorsQ = useQuery({ queryKey: qk.deployment.connectors(projectId), queryFn: () => listDeployConnectors(projectId), enabled: open });

  React.useEffect(() => {
    if (mode === "branch" && branchesQ.data?.length && !branch) {
      const def = branchesQ.data.find((b) => b.is_default) ?? branchesQ.data[0];
      if (def) setBranch(def.name);
    }
  }, [branchesQ.data, mode, branch]);
  React.useEffect(() => {
    if (!deployVia && connectorsQ.data?.connectors?.length) {
      const avail = connectorsQ.data.connectors.find((c) => c.available);
      if (avail) setDeployVia(avail.kind);
    }
  }, [connectorsQ.data, deployVia]);

  const prepare = useMutation({
    mutationFn: () =>
      prepareDeploy(projectId, {
        mode, ado_project: project!, repo_name: repo!,
        branch: mode === "branch" ? branch! : undefined,
        pr_id: mode === "pr" ? prId! : undefined,
        environment: env, deploy_via: deployVia || undefined,
        image_registry: registry || undefined, image_name: imageName || undefined,
        namespace: namespace || undefined,
      }),
    onSuccess: (r) => { toast.success(`Cloned ${r.repo_name} @ ${r.branch} · deploy via ${r.deploy_via}`); onPrepared(r); onOpenChange(false); },
    onError: (e) => toast.error("Couldn't prepare the deployment", { description: e instanceof Error ? e.message : undefined }),
  });

  const canSubmit = !!project && !!repo && (mode === "branch" ? !!branch : !!prId) && !prepare.isPending;
  const connectors = connectorsQ.data?.connectors ?? [];

  return (
    <Dialog open={open} onOpenChange={(v) => !prepare.isPending && onOpenChange(v)}>
      <DialogContent className="flex max-h-[88vh] max-w-lg flex-col">
        <DialogHeader>
          <DialogTitle className="font-display">Set up a deployment</DialogTitle>
          <DialogDescription>
            Pick the branch/PR and target. The agent clones read-only, detects your deploy
            connector, generates the package, and assesses readiness. Nothing is pushed until you open the PR.
          </DialogDescription>
        </DialogHeader>

        <div className="grid shrink-0 grid-cols-2 gap-2">
          <ModeBtn active={mode === "branch"} onClick={() => setMode("branch")} icon={GitBranch}>Branch</ModeBtn>
          <ModeBtn active={mode === "pr"} onClick={() => setMode("pr")} icon={GitPullRequest}>Open PR</ModeBtn>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
          <Step label="Project">
            <Cascade q={projectsQ} value={project} onChange={(v) => { setProject(v); setRepo(null); setBranch(null); setPrId(null); }}
              getKey={(p) => p.id} getValue={(p) => p.name} getLabel={(p) => p.name} empty="No Azure DevOps projects found." />
          </Step>
          {project && (
            <Step label="Repository">
              <Cascade q={reposQ} value={repo} onChange={(v) => { setRepo(v); setBranch(null); setPrId(null); if (!imageName) setImageName(v); }}
                getKey={(r) => r.id} getValue={(r) => r.name} getLabel={(r) => r.name} empty={`No repos in ${project}.`} />
            </Step>
          )}
          {project && repo && mode === "branch" && (
            <Step label="Branch">
              <Cascade q={branchesQ} value={branch} onChange={setBranch} getKey={(b) => b.name} getValue={(b) => b.name}
                getLabel={(b) => b.name} badge={(b) => (b.is_default ? "default" : undefined)} empty={`${repo} has no branches.`} />
            </Step>
          )}
          {project && repo && mode === "pr" && (
            <Step label="Open pull request">
              <Cascade q={prsQ} value={prId} onChange={setPrId} getKey={(p) => p.id} getValue={(p) => p.id}
                getLabel={(p) => `#${p.id} · ${p.title}`} badge={(p) => p.source_branch} empty={`No open PRs in ${repo}.`} />
            </Step>
          )}

          {project && repo && (
            <div className="space-y-3 rounded-lg border bg-surface-1 p-3">
              <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">Deployment config</p>
              <div className="grid grid-cols-2 gap-2">
                <Field label="Environment">
                  <Select value={env} onChange={setEnv} options={["dev", "staging", "production"]} />
                </Field>
                <Field label="Deploy via">
                  <Select value={deployVia || (connectors.find((c) => c.available)?.kind ?? "azure_pipelines")}
                    onChange={setDeployVia} options={connectors.map((c) => c.kind)} labels={Object.fromEntries(connectors.map((c) => [c.kind, c.label]))} />
                </Field>
                <Field label="Image registry"><Input value={registry} onChange={(e) => setRegistry(e.target.value)} placeholder="myregistry.azurecr.io" className="h-9 text-sm" /></Field>
                <Field label="Image name"><Input value={imageName} onChange={(e) => setImageName(e.target.value)} placeholder={repo ?? "app"} className="h-9 text-sm" /></Field>
                <Field label="Namespace"><Input value={namespace} onChange={(e) => setNamespace(e.target.value)} placeholder={`${repo ?? "app"}-${env}`} className="h-9 text-sm" /></Field>
              </div>
            </div>
          )}
        </div>

        <DialogFooter className="shrink-0">
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={prepare.isPending} className="border-line-soft">Cancel</Button>
          <Button onClick={() => prepare.mutate()} disabled={!canSubmit} aria-busy={prepare.isPending}
            className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-br font-semibold text-white">
            {prepare.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Rocket className="size-4" aria-hidden />}
            {prepare.isPending ? "Preparing…" : "Prepare deployment"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ModeBtn({ active, onClick, icon: Icon, children }: { active: boolean; onClick: () => void; icon: React.ComponentType<{ className?: string }>; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick}
      className={cn("flex items-center justify-center gap-2 rounded-lg border p-2.5 text-sm font-medium transition-colors",
        active ? "border-brand-bright/50 bg-brand-bright/10 text-brand-bright" : "border-line-soft bg-surface-1 text-muted-foreground hover:bg-surface-2")}>
      <Icon className="size-4" aria-hidden />{children}
    </button>
  );
}
function Step({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="space-y-2"><p className="text-muted-foreground text-xs font-medium uppercase tracking-wide">{label}</p>{children}</div>;
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="space-y-1"><Label className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</Label>{children}</div>;
}
function Select({ value, onChange, options, labels }: { value: string; onChange: (v: string) => void; options: string[]; labels?: Record<string, string> }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className="border-line-soft bg-surface-1 h-9 w-full rounded-md border px-2 text-sm">
      {options.map((o) => <option key={o} value={o}>{labels?.[o] ?? o}</option>)}
    </select>
  );
}

function Cascade<T>({ q, value, onChange, getKey, getValue, getLabel, badge, empty }: {
  q: { isLoading: boolean; isError: boolean; data?: T[] }; value: string | null; onChange: (v: string) => void;
  getKey: (i: T) => string; getValue: (i: T) => string; getLabel: (i: T) => string; badge?: (i: T) => string | undefined; empty: string;
}) {
  if (q.isLoading) return <LoadingState variant="list" rows={3} />;
  if (q.isError) return <p className="text-destructive text-sm">Couldn&apos;t reach Azure DevOps. Connect it on Integrations.</p>;
  if (!q.data || q.data.length === 0) return <p className="text-muted-foreground text-sm">{empty}</p>;
  return (
    <RadioGroup value={value ?? ""} onValueChange={onChange} className="max-h-48 space-y-1.5 overflow-auto">
      {q.data.map((item) => {
        const key = getKey(item); const b = badge?.(item);
        return (
          <Label key={key} htmlFor={key} className="border-line-soft bg-surface-1 hover:bg-surface-2 flex cursor-pointer items-center gap-3 rounded-lg border p-2.5 font-normal transition-colors">
            <RadioGroupItem value={getValue(item)} id={key} />
            <span className="flex min-w-0 flex-1 items-center justify-between gap-2">
              <span className="truncate text-sm font-medium">{getLabel(item)}</span>
              {b && <span className="text-muted-foreground shrink-0 font-mono text-[10px]">{b}</span>}
            </span>
          </Label>
        );
      })}
    </RadioGroup>
  );
}
