"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { GitBranch } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { LoadingState } from "@/components/ui/loading-state";
import { listAdoProjects, listAdoRepos, listAdoBranches } from "@/lib/api/dev-workspace";
import { qk } from "@/lib/api/query-keys";
import type { ProjectId } from "@/lib/schemas";

export interface TestTarget {
  ado_project: string;
  repo: string;
  branch: string;
}

export interface TestTargetDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: ProjectId;
  onSelected: (t: TestTarget) => void;
}

/** Import/select the code to test: ADO project → repo → branch. The clone happens
 * when the agent runs (clone_target), so this only selects. */
export function TestTargetDialog({ open, onOpenChange, projectId, onSelected }: TestTargetDialogProps) {
  const [project, setProject] = React.useState<string | null>(null);
  const [repo, setRepo] = React.useState<string | null>(null);
  const [branch, setBranch] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) { setProject(null); setRepo(null); setBranch(null); }
  }, [open]);

  const projectsQ = useQuery({
    queryKey: qk.devWorkspace.adoProjects(projectId),
    queryFn: () => listAdoProjects(projectId), enabled: open, staleTime: 30_000,
  });
  const reposQ = useQuery({
    queryKey: qk.devWorkspace.adoRepos(projectId, project ?? ""),
    queryFn: () => listAdoRepos(projectId, project!), enabled: open && !!project,
  });
  const branchesQ = useQuery({
    queryKey: qk.devWorkspace.adoBranches(projectId, project ?? "", repo ?? ""),
    queryFn: () => listAdoBranches(projectId, project!, repo!), enabled: open && !!project && !!repo,
  });
  React.useEffect(() => {
    if (branchesQ.data && branchesQ.data.length > 0 && !branch) {
      const def = branchesQ.data.find((b) => b.is_default) ?? branchesQ.data[0];
      if (def) setBranch(def.name);
    }
  }, [branchesQ.data, branch]);

  const canSubmit = !!project && !!repo && !!branch;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-lg flex-col">
        <DialogHeader>
          <DialogTitle className="font-display">Import code to test</DialogTitle>
          <DialogDescription>
            Pick the Azure DevOps project, repo, and branch. The agent clones it read-only when a test run starts.
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto pr-1">
          <Step label="Project">
            <Cascade q={projectsQ} value={project} onChange={(v) => { setProject(v); setRepo(null); setBranch(null); }}
              getKey={(p) => p.id} getValue={(p) => p.name} getLabel={(p) => p.name} empty="No Azure DevOps projects found." />
          </Step>
          {project && (
            <Step label="Repository">
              <Cascade q={reposQ} value={repo} onChange={(v) => { setRepo(v); setBranch(null); }}
                getKey={(r) => r.id} getValue={(r) => r.name} getLabel={(r) => r.name} empty={`No repositories in ${project}.`} />
            </Step>
          )}
          {project && repo && (
            <Step label="Branch">
              <Cascade q={branchesQ} value={branch} onChange={setBranch}
                getKey={(b) => b.name} getValue={(b) => b.name} getLabel={(b) => b.name}
                badge={(b) => (b.is_default ? "default" : undefined)} empty={`${repo} has no branches.`} />
            </Step>
          )}
        </div>

        <DialogFooter className="shrink-0">
          <Button variant="outline" onClick={() => onOpenChange(false)} className="border-line-soft">Cancel</Button>
          <Button
            onClick={() => { onSelected({ ado_project: project!, repo: repo!, branch: branch! }); onOpenChange(false); }}
            disabled={!canSubmit}
            className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-br font-semibold text-white"
          >
            <GitBranch className="size-4" aria-hidden />
            Use this branch
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Step({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <p className="text-muted-foreground text-xs font-medium uppercase tracking-wide">{label}</p>
      {children}
    </div>
  );
}

function Cascade<T>({ q, value, onChange, getKey, getValue, getLabel, badge, empty }: {
  q: { isLoading: boolean; isError: boolean; data?: T[] };
  value: string | null; onChange: (v: string) => void;
  getKey: (i: T) => string; getValue: (i: T) => string; getLabel: (i: T) => string;
  badge?: (i: T) => string | undefined; empty: string;
}) {
  if (q.isLoading) return <LoadingState variant="list" rows={3} />;
  if (q.isError) return <p className="text-destructive text-sm">Couldn&apos;t reach Azure DevOps. Connect it on Integrations.</p>;
  if (!q.data || q.data.length === 0) return <p className="text-muted-foreground text-sm">{empty}</p>;
  return (
    <RadioGroup value={value ?? ""} onValueChange={onChange} className="max-h-56 space-y-1.5 overflow-auto">
      {q.data.map((item) => {
        const key = getKey(item); const b = badge?.(item);
        return (
          <Label key={key} htmlFor={key}
            className="border-line-soft bg-surface-1 hover:bg-surface-2 flex cursor-pointer items-center gap-3 rounded-lg border p-2.5 font-normal transition-colors">
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
