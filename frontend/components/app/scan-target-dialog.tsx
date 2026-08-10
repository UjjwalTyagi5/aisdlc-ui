"use client";

import * as React from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { GitBranch, GitPullRequest, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";
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
import { listOpenPrs, prepareScan } from "@/lib/api/security";
import { qk } from "@/lib/api/query-keys";
import type { PrepareScanResult } from "@/lib/schemas/security";
import type { ProjectId } from "@/lib/schemas";

type Mode = "branch" | "pr";

export interface ScanTargetDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: ProjectId;
  onPrepared: (result: PrepareScanResult) => void;
}

/** Pick what to scan: a branch OR an open PR. Cascade: mode → project → repo → branch|PR. */
export function ScanTargetDialog({
  open,
  onOpenChange,
  projectId,
  onPrepared,
}: ScanTargetDialogProps) {
  const [mode, setMode] = React.useState<Mode>("branch");
  const [project, setProject] = React.useState<string | null>(null);
  const [repo, setRepo] = React.useState<string | null>(null);
  const [branch, setBranch] = React.useState<string | null>(null);
  const [prId, setPrId] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) {
      setMode("branch");
      setProject(null);
      setRepo(null);
      setBranch(null);
      setPrId(null);
    }
  }, [open]);

  const projectsQ = useQuery({
    queryKey: qk.devWorkspace.adoProjects(projectId),
    queryFn: () => listAdoProjects(projectId),
    enabled: open,
    staleTime: 30_000,
  });
  const reposQ = useQuery({
    queryKey: qk.devWorkspace.adoRepos(projectId, project ?? ""),
    queryFn: () => listAdoRepos(projectId, project!),
    enabled: open && !!project,
  });
  const branchesQ = useQuery({
    queryKey: qk.devWorkspace.adoBranches(projectId, project ?? "", repo ?? ""),
    queryFn: () => listAdoBranches(projectId, project!, repo!),
    enabled: open && mode === "branch" && !!project && !!repo,
  });
  const prsQ = useQuery({
    queryKey: qk.security.prs(projectId, project ?? "", repo ?? ""),
    queryFn: () => listOpenPrs(projectId, project!, repo!),
    enabled: open && mode === "pr" && !!project && !!repo,
  });

  React.useEffect(() => {
    if (mode === "branch" && branchesQ.data && branchesQ.data.length > 0 && !branch) {
      const def = branchesQ.data.find((b) => b.is_default) ?? branchesQ.data[0];
      if (def) setBranch(def.name);
    }
  }, [branchesQ.data, mode, branch]);

  const prepare = useMutation({
    mutationFn: () =>
      prepareScan(projectId, {
        mode,
        ado_project: project!,
        repo_name: repo!,
        branch: mode === "branch" ? branch! : undefined,
        pr_id: mode === "pr" ? prId! : undefined,
      }),
    onSuccess: (result) => {
      toast.success(`Cloned ${result.repo_name} @ ${result.branch} — ready to scan`);
      onPrepared(result);
      onOpenChange(false);
    },
    onError: (err) =>
      toast.error("Couldn't prepare the scan", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const canSubmit =
    !!project && !!repo && (mode === "branch" ? !!branch : !!prId) && !prepare.isPending;

  return (
    <Dialog open={open} onOpenChange={(v) => !prepare.isPending && onOpenChange(v)}>
      <DialogContent className="flex max-h-[85vh] max-w-lg flex-col">
        <DialogHeader>
          <DialogTitle className="font-display">Select a scan target</DialogTitle>
          <DialogDescription>
            Scan a branch or a pull request&apos;s source branch. The repo is cloned
            read-only — nothing is modified.
          </DialogDescription>
        </DialogHeader>

        <div className="grid shrink-0 grid-cols-2 gap-2">
          <ModeButton active={mode === "branch"} onClick={() => setMode("branch")} icon={GitBranch}>
            Branch
          </ModeButton>
          <ModeButton active={mode === "pr"} onClick={() => setMode("pr")} icon={GitPullRequest}>
            Open PR
          </ModeButton>
        </div>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto pr-1">
          <Step label="Project">
            <CascadeList
              q={projectsQ}
              value={project}
              onChange={(v) => {
                setProject(v);
                setRepo(null);
                setBranch(null);
                setPrId(null);
              }}
              getKey={(p) => p.id}
              getValue={(p) => p.name}
              getLabel={(p) => p.name}
              emptyText="No Azure DevOps projects found."
            />
          </Step>

          {project && (
            <Step label="Repository">
              <CascadeList
                q={reposQ}
                value={repo}
                onChange={(v) => {
                  setRepo(v);
                  setBranch(null);
                  setPrId(null);
                }}
                getKey={(r) => r.id}
                getValue={(r) => r.name}
                getLabel={(r) => r.name}
                emptyText={`No repositories in ${project}.`}
              />
            </Step>
          )}

          {project && repo && mode === "branch" && (
            <Step label="Branch to scan">
              <CascadeList
                q={branchesQ}
                value={branch}
                onChange={setBranch}
                getKey={(b) => b.name}
                getValue={(b) => b.name}
                getLabel={(b) => b.name}
                badge={(b) => (b.is_default ? "default" : undefined)}
                emptyText={`${repo} has no branches.`}
              />
            </Step>
          )}

          {project && repo && mode === "pr" && (
            <Step label="Open pull request">
              <CascadeList
                q={prsQ}
                value={prId}
                onChange={setPrId}
                getKey={(p) => p.id}
                getValue={(p) => p.id}
                getLabel={(p) => `#${p.id} · ${p.title}`}
                badge={(p) => p.source_branch}
                emptyText={`No open pull requests in ${repo}.`}
              />
            </Step>
          )}
        </div>

        <DialogFooter className="shrink-0">
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={prepare.isPending}
            className="border-line-soft"
          >
            Cancel
          </Button>
          <Button
            onClick={() => prepare.mutate()}
            disabled={!canSubmit}
            aria-busy={prepare.isPending}
            className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-br font-semibold text-white"
          >
            {prepare.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
            {prepare.isPending ? "Cloning…" : "Prepare scan"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ModeButton({
  active,
  onClick,
  icon: Icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex items-center justify-center gap-2 rounded-lg border p-2.5 text-sm font-medium transition-colors",
        active
          ? "border-brand-bright/50 bg-brand-bright/10 text-brand-bright"
          : "border-line-soft bg-surface-1 text-muted-foreground hover:bg-surface-2",
      )}
    >
      <Icon className="size-4" aria-hidden />
      {children}
    </button>
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

interface CascadeListProps<T> {
  q: { isLoading: boolean; isError: boolean; data?: T[] };
  value: string | null;
  onChange: (v: string) => void;
  getKey: (item: T) => string;
  getValue: (item: T) => string;
  getLabel: (item: T) => string;
  badge?: (item: T) => string | undefined;
  emptyText: string;
}

function CascadeList<T>({
  q,
  value,
  onChange,
  getKey,
  getValue,
  getLabel,
  badge,
  emptyText,
}: CascadeListProps<T>) {
  if (q.isLoading) return <LoadingState variant="list" rows={3} />;
  if (q.isError)
    return (
      <p className="text-destructive text-sm">
        Couldn&apos;t reach Azure DevOps. Connect it on the Integrations page.
      </p>
    );
  if (!q.data || q.data.length === 0)
    return <p className="text-muted-foreground text-sm">{emptyText}</p>;
  return (
    <RadioGroup
      value={value ?? ""}
      onValueChange={onChange}
      className="max-h-56 space-y-1.5 overflow-auto"
    >
      {q.data.map((item) => {
        const key = getKey(item);
        const b = badge?.(item);
        return (
          <Label
            key={key}
            htmlFor={key}
            className="border-line-soft bg-surface-1 hover:bg-surface-2 flex cursor-pointer items-center gap-3 rounded-lg border p-2.5 font-normal transition-colors"
          >
            <RadioGroupItem value={getValue(item)} id={key} />
            <span className="flex min-w-0 flex-1 items-center justify-between gap-2">
              <span className="truncate text-sm font-medium">{getLabel(item)}</span>
              {b && (
                <span className="text-muted-foreground shrink-0 font-mono text-[10px]">{b}</span>
              )}
            </span>
          </Label>
        );
      })}
    </RadioGroup>
  );
}
