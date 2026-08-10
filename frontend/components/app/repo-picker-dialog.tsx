"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";

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
import { Input } from "@/components/ui/input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { LoadingState } from "@/components/ui/loading-state";
import {
  listAdoProjects,
  listAdoRepos,
  listAdoBranches,
  pullRepo,
} from "@/lib/api/dev-workspace";
import { qk } from "@/lib/api/query-keys";
import type { ProjectId } from "@/lib/schemas";

export interface RepoPickerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: ProjectId;
  /** Called after a successful pull so the page can refresh the indicator + PRs. */
  onPulled: () => void;
}

/**
 * Three-step cascade modal: Azure DevOps Project → Repository → Branch.
 * Each step is unlocked only once the prior selection is made. Selecting
 * a project resets the repo and branch; selecting a repo resets the branch.
 * The Pull button is enabled only when all three are chosen.
 */
export function RepoPickerDialog({
  open,
  onOpenChange,
  projectId,
  onPulled,
}: RepoPickerDialogProps) {
  const queryClient = useQueryClient();

  const [project, setProject] = React.useState<string | null>(null);
  const [repo, setRepo] = React.useState<string | null>(null);
  const [branch, setBranch] = React.useState<string | null>(null);
  // Name typed when a repo has no branches yet — created on pull so the agent
  // can start working on an empty repo.
  const [newBranch, setNewBranch] = React.useState("main");

  // Reset all state when the dialog closes.
  React.useEffect(() => {
    if (!open) {
      setProject(null);
      setRepo(null);
      setBranch(null);
      setNewBranch("main");
    }
  }, [open]);

  // — Step 1: ADO Projects —
  const projectsQ = useQuery({
    queryKey: qk.devWorkspace.adoProjects(projectId),
    queryFn: () => listAdoProjects(projectId),
    enabled: open,
    staleTime: 30_000,
  });

  // — Step 2: Repositories (enabled once a project is chosen) —
  const reposQ = useQuery({
    queryKey: qk.devWorkspace.adoRepos(projectId, project ?? ""),
    queryFn: () => listAdoRepos(projectId, project!),
    enabled: open && !!project,
    staleTime: 0,
  });

  // — Step 3: Branches (enabled once a repo is chosen) —
  const branchesQ = useQuery({
    queryKey: qk.devWorkspace.adoBranches(projectId, project ?? "", repo ?? ""),
    queryFn: () => listAdoBranches(projectId, project!, repo!),
    enabled: open && !!project && !!repo,
    staleTime: 0,
  });

  // Auto-select the default branch when branches load.
  React.useEffect(() => {
    if (branchesQ.data && branchesQ.data.length > 0) {
      const defaultBranch =
        branchesQ.data.find((b) => b.is_default) ?? branchesQ.data[0];
      if (defaultBranch) setBranch(defaultBranch.name);
    }
  }, [branchesQ.data]);

  // No branches → the repo is empty; the user names a branch to create.
  const hasBranches = !!branchesQ.data && branchesQ.data.length > 0;
  const branchesReady = !branchesQ.isLoading && !branchesQ.isError;
  const effectiveBranch =
    hasBranches ? branch : branchesReady ? newBranch.trim() || null : null;

  // — Pull mutation —
  const pull = useMutation({
    mutationFn: () =>
      pullRepo(projectId, {
        ado_project: project!,
        repo_name: repo!,
        branch: effectiveBranch!,
      }),
    onSuccess: (ws) => {
      if (ws.status === "error") {
        toast.error("Pull failed", { description: ws.error });
        return;
      }
      toast.success(`Pulled ${repo} @ ${effectiveBranch}`);
      queryClient.invalidateQueries({
        queryKey: qk.devWorkspace.workspace(projectId),
      });
      queryClient.invalidateQueries({
        queryKey: qk.devWorkspace.prs(projectId),
      });
      onPulled();
      onOpenChange(false);
    },
    onError: (err) =>
      toast.error("Couldn't pull the repo", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const handleProjectChange = (value: string) => {
    setProject(value);
    setRepo(null);
    setBranch(null);
  };

  const handleRepoChange = (value: string) => {
    setRepo(value);
    setBranch(null);
    setNewBranch("main");
  };

  const canPull = !!project && !!repo && !!effectiveBranch && !pull.isPending;

  return (
    <Dialog open={open} onOpenChange={(v) => !pull.isPending && onOpenChange(v)}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-display">Pull from Azure Repos</DialogTitle>
          <DialogDescription>
            Choose a project, repository, and branch to clone into this
            project&apos;s Development workspace.
          </DialogDescription>
        </DialogHeader>

        {/* Breadcrumb trail — shown once at least the project is chosen */}
        {project && (
          <p className="text-muted-foreground -mt-1 truncate text-xs">
            {project}
            {repo && (
              <>
                <span className="mx-1 opacity-50">/</span>
                {repo}
              </>
            )}
            {effectiveBranch && (
              <>
                <span className="mx-1 opacity-50">@</span>
                {effectiveBranch}
                {!hasBranches && branchesReady && (
                  <span className="ml-1 opacity-60">(new)</span>
                )}
              </>
            )}
          </p>
        )}

        <div className="min-h-[16rem] space-y-5">
          {/* ── Step 1: Project ── */}
          <StepSection label="Project">
            {projectsQ.isLoading ? (
              <LoadingState variant="list" rows={3} />
            ) : projectsQ.isError ? (
              <p className="text-destructive text-sm">
                Couldn&apos;t reach Azure DevOps. Connect a provider on the
                Integrations page.
              </p>
            ) : !projectsQ.data || projectsQ.data.length === 0 ? (
              <p className="text-muted-foreground text-sm">
                No projects found on the connected Azure DevOps organisation.
              </p>
            ) : (
              <RadioGroup
                value={project ?? ""}
                onValueChange={handleProjectChange}
                className="max-h-72 space-y-1.5 overflow-auto"
                aria-label="Azure DevOps project"
              >
                {projectsQ.data.map((p) => {
                  const rid = `ado-project-${p.id}`;
                  return (
                    <Label
                      key={p.id}
                      htmlFor={rid}
                      className="border-line-soft bg-surface-1 hover:bg-surface-2 flex cursor-pointer items-center gap-3 rounded-lg border p-3 font-normal transition-colors"
                    >
                      <RadioGroupItem value={p.name} id={rid} />
                      <span className="truncate text-sm font-medium">
                        {p.name}
                      </span>
                    </Label>
                  );
                })}
              </RadioGroup>
            )}
          </StepSection>

          {/* ── Step 2: Repository (visible once project selected) ── */}
          {project && (
            <StepSection label="Repository">
              {reposQ.isLoading ? (
                <LoadingState variant="list" rows={3} />
              ) : reposQ.isError ? (
                <p className="text-destructive text-sm">
                  Couldn&apos;t load repositories for{" "}
                  <span className="font-medium">{project}</span>.
                </p>
              ) : !reposQ.data || reposQ.data.length === 0 ? (
                <p className="text-muted-foreground text-sm">
                  No repositories found in{" "}
                  <span className="font-medium">{project}</span>.
                </p>
              ) : (
                <RadioGroup
                  value={repo ?? ""}
                  onValueChange={handleRepoChange}
                  className="max-h-72 space-y-1.5 overflow-auto"
                  aria-label="Repository"
                >
                  {reposQ.data.map((r) => {
                    const rid = `ado-repo-${r.id}`;
                    return (
                      <Label
                        key={r.id}
                        htmlFor={rid}
                        className="border-line-soft bg-surface-1 hover:bg-surface-2 flex cursor-pointer items-center gap-3 rounded-lg border p-3 font-normal transition-colors"
                      >
                        <RadioGroupItem value={r.name} id={rid} />
                        <span className="truncate text-sm font-medium">
                          {r.name}
                        </span>
                      </Label>
                    );
                  })}
                </RadioGroup>
              )}
            </StepSection>
          )}

          {/* ── Step 3: Branch (visible once repo selected) ── */}
          {project && repo && (
            <StepSection label="Branch">
              {branchesQ.isLoading ? (
                <LoadingState variant="list" rows={3} />
              ) : branchesQ.isError ? (
                <p className="text-destructive text-sm">
                  Couldn&apos;t load branches for{" "}
                  <span className="font-medium">{repo}</span>.
                </p>
              ) : !branchesQ.data || branchesQ.data.length === 0 ? (
                <div className="space-y-2">
                  <p className="text-muted-foreground text-sm">
                    <span className="font-medium">{repo}</span> has no branches yet
                    — name a branch to create and start working in it.
                  </p>
                  <Input
                    value={newBranch}
                    onChange={(e) => setNewBranch(e.target.value)}
                    placeholder="main"
                    aria-label="New branch name"
                    spellCheck={false}
                    className="font-mono text-sm"
                  />
                </div>
              ) : (
                <RadioGroup
                  value={branch ?? ""}
                  onValueChange={setBranch}
                  className="max-h-72 space-y-1.5 overflow-auto"
                  aria-label="Branch"
                >
                  {branchesQ.data.map((b) => {
                    const rid = `ado-branch-${b.name.replace(/\//g, "-")}`;
                    return (
                      <Label
                        key={b.name}
                        htmlFor={rid}
                        className="border-line-soft bg-surface-1 hover:bg-surface-2 flex cursor-pointer items-center gap-3 rounded-lg border p-3 font-normal transition-colors"
                      >
                        <RadioGroupItem value={b.name} id={rid} />
                        <span className="flex min-w-0 flex-1 items-center justify-between gap-2">
                          <span className="truncate text-sm font-medium">
                            {b.name}
                          </span>
                          {b.is_default && (
                            <span className="text-muted-foreground shrink-0 font-mono text-[11px]">
                              default
                            </span>
                          )}
                        </span>
                      </Label>
                    );
                  })}
                </RadioGroup>
              )}
            </StepSection>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={pull.isPending}
            className="border-line-soft"
          >
            Cancel
          </Button>
          <Button
            onClick={() => pull.mutate()}
            disabled={!canPull}
            aria-busy={pull.isPending}
            className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-br font-semibold text-white shadow-[0_4px_12px_-4px_oklch(0.6_0.2_35_/_0.5)] transition-shadow hover:shadow-[0_8px_20px_-6px_oklch(0.6_0.2_35_/_0.65)]"
          >
            {pull.isPending ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : null}
            {pull.isPending
              ? "Pulling…"
              : !hasBranches && branchesReady && project && repo
                ? "Create branch & pull"
                : "Pull repo"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Thin labelled wrapper that groups each step with a consistent header. */
function StepSection({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-2">
      <p className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
        {label}
      </p>
      {children}
    </div>
  );
}
