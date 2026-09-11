"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, FolderGit2, GitCommitHorizontal, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  getLegacyCode,
  listLegacyRepositories,
  pullLegacyCode,
  type Track3Stage,
} from "@/lib/api/modernization";
import { qk } from "@/lib/api/query-keys";
import type { ProjectId } from "@/lib/schemas";
import type { LegacyCodeRecord } from "@/lib/schemas/modernization";
import { cn } from "@/lib/utils";

/**
 * The project's legacy code, as both Track 3 pages show it: what is pulled (repository,
 * commit, when), whether a pull is running, and the Pull dialog.
 *
 * ONE CHECKOUT PER PROJECT. Pulling from the Requirements page or the Discovery page
 * fills the same checkout — both agents read it — so the status is keyed by project,
 * not by page. While a pull runs the status is polled; the page learns it finished
 * without anyone refreshing.
 */
export function useLegacyCode(projectId: ProjectId, stage: Track3Stage) {
  return useQuery({
    queryKey: qk.modernization.legacyCode(projectId),
    queryFn: () => getLegacyCode(projectId, stage),
    refetchInterval: (query) => (query.state.data?.status === "pulling" ? 3000 : false),
  });
}

function repoLabel(url: string | undefined) {
  return (url ?? "").replace(/\/+$/, "").split("/").pop()?.replace(/\.git$/, "") || "repository";
}

export function LegacyCodeStatus({ record, className }: { record?: LegacyCodeRecord; className?: string }) {
  if (!record || (record.status === "none" && !record.pull)) {
    return <span className={cn("text-muted-foreground text-xs", className)}>No legacy code pulled yet</span>;
  }
  if (record.status === "pulling") {
    return (
      <span className={cn("text-muted-foreground inline-flex items-center gap-1.5 text-xs", className)}>
        <Loader2 className="size-3 animate-spin" aria-hidden />
        Pulling {repoLabel(record.request?.url)}…
      </span>
    );
  }
  const pull = record.pull;
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs", className)}>
      {record.status === "failed" && (
        <span className="text-destructive inline-flex items-center gap-1" title={record.error}>
          <AlertTriangle className="size-3" aria-hidden />
          Last pull failed
        </span>
      )}
      {pull && (
        <span className="text-muted-foreground inline-flex items-center gap-1" title={pull.url}>
          <FolderGit2 className="size-3" aria-hidden />
          <span className="text-foreground font-medium">{pull.name || repoLabel(pull.url)}</span>
          {pull.commit && (
            <>
              <GitCommitHorizontal className="size-3" aria-hidden />
              <span className="font-mono">{pull.commit.slice(0, 7)}</span>
            </>
          )}
          {pull.pulledAt && <span>· pulled {new Date(pull.pulledAt).toLocaleString()}</span>}
        </span>
      )}
    </span>
  );
}

/**
 * Says so when a pull the user started finishes — the dialog closed long ago, and a
 * large repository takes a minute or two.
 */
export function useAnnouncePullOutcome(record?: LegacyCodeRecord) {
  const previous = React.useRef<string | undefined>(undefined);
  React.useEffect(() => {
    const status = record?.status;
    if (previous.current === "pulling" && status === "ready") {
      toast.success(`Legacy code pulled — ${record?.pull?.name ?? "the repository"} is ready for the agents.`);
    } else if (previous.current === "pulling" && status === "failed") {
      toast.error(record?.error || "Pulling the legacy code failed.");
    }
    previous.current = status;
  }, [record?.status, record?.pull?.name, record?.error]);
}

export function PullLegacyCodeDialog({
  projectId,
  stage,
  open,
  onOpenChange,
  current,
}: {
  projectId: ProjectId;
  stage: Track3Stage;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  current?: LegacyCodeRecord;
}) {
  const queryClient = useQueryClient();
  const [adoProject, setAdoProject] = React.useState("");
  const [url, setUrl] = React.useState("");
  const [branch, setBranch] = React.useState("");
  const [picked, setPicked] = React.useState<string | null>(null);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    if (open) {
      setUrl(current?.pull?.url ?? "");
      setBranch(current?.pull?.branch ?? "");
      setPicked(null);
      setError("");
    }
  }, [open, current?.pull?.url, current?.pull?.branch]);

  const reposQ = useQuery({
    queryKey: qk.modernization.legacyRepositories(projectId, stage, adoProject),
    queryFn: () => listLegacyRepositories(projectId, stage, adoProject),
    enabled: open,
    staleTime: 60_000,
  });

  const pull = useMutation({
    mutationFn: () => pullLegacyCode(projectId, { url: url.trim(), branch: branch.trim(), stage }),
    onSuccess: (record) => {
      queryClient.setQueryData(qk.modernization.legacyCode(projectId), record);
      toast.info(`Pulling ${repoLabel(url)} — a large repository takes a minute or two.`);
      onOpenChange(false);
    },
    onError: (e: Error) => setError(e.message || "The pull could not be started."),
  });

  const data = reposQ.data;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Pull legacy code</DialogTitle>
          <DialogDescription>
            The repository is cloned read-only for this project. Both Code Modernization agents read it:
            Requirements before it asks you about today&apos;s system, Discovery when it assesses.
          </DialogDescription>
        </DialogHeader>

        <section aria-label="Connected repositories" className="space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">From the project&apos;s connection</p>
            {adoProject && (
              <Button variant="ghost" size="sm" onClick={() => setAdoProject("")}>
                <ArrowLeft className="size-3.5" aria-hidden />
                All projects
              </Button>
            )}
          </div>
          <div className="max-h-48 overflow-auto rounded-md border">
            {reposQ.isLoading ? (
              <p className="text-muted-foreground p-3 text-xs">Looking for connected repositories…</p>
            ) : reposQ.isError ? (
              <p className="text-muted-foreground p-3 text-xs">
                {reposQ.error instanceof Error ? reposQ.error.message : "Could not list repositories."}
              </p>
            ) : data?.projects ? (
              data.projects.length ? (
                <ul>
                  {data.projects.map((name) => (
                    <li key={name}>
                      <button
                        type="button"
                        className="hover:bg-muted w-full px-3 py-2 text-left text-sm"
                        onClick={() => setAdoProject(name)}
                      >
                        {name}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-muted-foreground p-3 text-xs">No Azure DevOps projects are visible.</p>
              )
            ) : data?.repositories ? (
              data.repositories.length ? (
                <ul>
                  {data.repositories.map((r) => (
                    <li key={r.name}>
                      <button
                        type="button"
                        aria-pressed={picked === r.name}
                        className={cn(
                          "hover:bg-muted flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm",
                          picked === r.name && "bg-muted",
                        )}
                        onClick={() => {
                          setPicked(r.name);
                          setUrl(r.url);
                          setBranch(r.defaultBranch);
                        }}
                      >
                        <span className="truncate">{r.name}</span>
                        {r.defaultBranch && (
                          <span className="text-muted-foreground font-mono text-[11px]">{r.defaultBranch}</span>
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-muted-foreground p-3 text-xs">No repositories found.</p>
              )
            ) : (
              <p className="text-muted-foreground p-3 text-xs">
                {data?.problem || "No repository connection is wired to this project."}
              </p>
            )}
          </div>
        </section>

        <section aria-label="Repository URL" className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="legacy-url">Or the repository&apos;s https URL</Label>
            <Input
              id="legacy-url"
              placeholder="https://github.com/org/legacy-system"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                setPicked(null);
              }}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="legacy-branch">Branch</Label>
            <Input
              id="legacy-branch"
              placeholder="default branch"
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
            />
          </div>
          <p className="text-muted-foreground text-xs">
            GitHub, Azure DevOps, GitLab or Bitbucket. A private repository uses the project&apos;s connection
            for that host; a public one needs none.
          </p>
          {error && <p className="text-destructive text-sm">{error}</p>}
        </section>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => pull.mutate()} disabled={!url.trim() || pull.isPending}>
            {pull.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
            Pull legacy code
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
