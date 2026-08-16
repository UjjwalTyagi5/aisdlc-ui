"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ExternalLink,
  FileCode2,
  Files,
  GitBranch,
  GitPullRequest,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";

import { AgentChatDrawer } from "@/components/app/agent-chat-drawer";
import { ModelSelector } from "@/components/app/model-selector";
import { RepoPickerDialog } from "@/components/app/repo-picker-dialog";
import { RepoFileTree } from "@/components/app/repo-file-tree";
import { CodeViewer } from "@/components/app/code-viewer";
import { RequireRole } from "@/components/auth/require-role";

import { useAgentChat } from "@/hooks/use-agent-chat";
import { useSession } from "@/hooks/use-session";
import {
  getWorkspace,
  getWorkspaceTree,
  getWorkspaceFile,
  getWorkspaceChanges,
  getFileChangedLines,
  listDevPrs,
} from "@/lib/api/dev-workspace";
import { getProject } from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import type { ChangeStatus } from "@/components/app/repo-file-tree";
import type { DevPr, ProjectId } from "@/lib/schemas";

type LeftTab = "files" | "prs";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export default function DevelopmentPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id as ProjectId;
  const queryClient = useQueryClient();
  useSession({ required: true });

  const projectQ = useQuery({
    queryKey: qk.projects.detail(projectId),
    queryFn: () => getProject(projectId),
  });

  const workspaceQ = useQuery({
    queryKey: qk.devWorkspace.workspace(projectId),
    queryFn: () => getWorkspace(projectId),
  });
  const workspace = workspaceQ.data ?? null;
  const ready = workspace?.status === "ready";

  const devPrsQ = useQuery({
    queryKey: qk.devWorkspace.prs(projectId),
    queryFn: () => listDevPrs(projectId),
  });
  // Memoised so the `?? []` fallback doesn't produce a new array identity on
  // every render and re-run the bucketing below.
  const devPrs = React.useMemo(() => devPrsQ.data ?? [], [devPrsQ.data]);

  const buckets = React.useMemo(() => {
    const open: DevPr[] = [];
    const review: DevPr[] = [];
    const merged: DevPr[] = [];
    for (const p of devPrs) {
      if (p.status === "merged") merged.push(p);
      else if (p.status === "review") review.push(p);
      else open.push(p);
    }
    return { open, review, merged };
  }, [devPrs]);

  // ── Repo explorer ───────────────────────────────────────────────────────────
  const [leftTab, setLeftTab] = React.useState<LeftTab>("files");
  const [selectedFile, setSelectedFile] = React.useState<string | null>(null);

  const treeQ = useQuery({
    queryKey: qk.devWorkspace.tree(projectId),
    queryFn: () => getWorkspaceTree(projectId),
    enabled: ready,
  });

  const changesQ = useQuery({
    queryKey: qk.devWorkspace.changes(projectId),
    queryFn: () => getWorkspaceChanges(projectId),
    enabled: ready,
  });
  const changesMap = React.useMemo(() => {
    const m = new Map<string, ChangeStatus>();
    for (const f of changesQ.data?.files ?? []) m.set(f.path, f.status);
    return m;
  }, [changesQ.data]);

  const fileQ = useQuery({
    queryKey: selectedFile ? qk.devWorkspace.file(projectId, selectedFile) : ["dev-file", "none"],
    queryFn: () => getWorkspaceFile(projectId, selectedFile!),
    enabled: ready && !!selectedFile,
    staleTime: 5 * 60_000,
  });

  const changedLinesQ = useQuery({
    queryKey: selectedFile
      ? qk.devWorkspace.changedLines(projectId, selectedFile)
      : ["dev-changed-lines", "none"],
    queryFn: () => getFileChangedLines(projectId, selectedFile!),
    enabled: ready && !!selectedFile && changesMap.has(selectedFile),
  });

  // Refresh everything the agent may have touched: file tree, change decorations,
  // open file content + its changed lines, PRs, and workspace state.
  const refreshWorkspace = React.useCallback(() => {
    queryClient.invalidateQueries({ queryKey: qk.devWorkspace.tree(projectId) });
    queryClient.invalidateQueries({ queryKey: qk.devWorkspace.changes(projectId) });
    queryClient.invalidateQueries({ queryKey: ["dev-workspace", "file", projectId] });
    queryClient.invalidateQueries({ queryKey: ["dev-workspace", "changed-lines", projectId] });
    queryClient.invalidateQueries({ queryKey: qk.devWorkspace.prs(projectId) });
    queryClient.invalidateQueries({ queryKey: qk.devWorkspace.workspace(projectId) });
  }, [queryClient, projectId]);

  const [chatOpen, setChatOpen] = React.useState(false);
  const [agentModel, setAgentModel] = React.useState<string>();
  const [repoPickerOpen, setRepoPickerOpen] = React.useState(false);
  const chat = useAgentChat({
    agent: "development",
    projectId,
    sessionKey: workspace?.commit_sha ?? projectId,
    onArtifact: refreshWorkspace,
    context: { page: "Development", project_id: projectId },
  });

  // After each agent turn finishes (busy → idle), re-pull the workspace view so
  // edits the agent made on disk surface as change decorations + line highlights.
  const prevBusy = React.useRef(chat.busy);
  React.useEffect(() => {
    if (prevBusy.current && !chat.busy) refreshWorkspace();
    prevBusy.current = chat.busy;
  }, [chat.busy, refreshWorkspace]);

  // -------- render --------
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
          description={projectQ.error instanceof Error ? projectQ.error.message : "Unknown error."}
          onRetry={() => projectQ.refetch()}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b px-4 py-3 md:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Development</h1>
            <p className="text-muted-foreground text-xs">
              {workspaceQ.isLoading ? null : ready ? (
                <span className="inline-flex items-center gap-1">
                  <GitBranch className="size-3" aria-hidden />
                  Currently pulled:{" "}
                  <span className="font-mono">{`${workspace.ado_project} / ${workspace.repo_name}`}</span>
                  {" @ "}
                  <span className="font-mono">{workspace.branch}</span>
                </span>
              ) : (
                <span>No repo pulled</span>
              )}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ModelSelector
              aria-label="Development agent model"
              projectId={projectId}
              value={agentModel}
              onValueChange={setAgentModel}
            />
            <Button variant="outline" size="sm" onClick={() => setRepoPickerOpen(true)}>
              <GitBranch className="size-4" aria-hidden />
              Pull repos
            </Button>
            <RequireRole capability="run:trigger">
              <Button size="sm" onClick={() => setChatOpen(true)}>
                <GitPullRequest className="size-4" aria-hidden />
                Run Dev agent
              </Button>
            </RequireRole>
          </div>
        </div>
      </div>

      <div className="grid flex-1 gap-0 overflow-hidden md:grid-cols-[300px_1fr] xl:grid-cols-[340px_1fr]">
        {/* Left panel — repo explorer / PRs */}
        <aside className="flex min-h-0 flex-col overflow-hidden border-b md:border-b-0 md:border-r">
          {/* Segmented header */}
          <div className="flex items-center gap-1 border-b p-2">
            <SegBtn active={leftTab === "files"} onClick={() => setLeftTab("files")} icon={Files}>
              Explorer
            </SegBtn>
            <SegBtn active={leftTab === "prs"} onClick={() => setLeftTab("prs")} icon={GitPullRequest}>
              PRs
              {devPrs.length > 0 && (
                <span className="bg-muted text-muted-foreground ml-1 rounded-full px-1.5 text-[10px]">
                  {devPrs.length}
                </span>
              )}
            </SegBtn>
          </div>

          {leftTab === "files" ? (
            <div className="min-h-0 flex-1 overflow-auto">
              {!ready ? (
                <div className="text-muted-foreground p-4 text-center text-xs">
                  <Files className="mx-auto mb-2 size-5 opacity-60" aria-hidden />
                  {workspace?.status === "pulling"
                    ? "Pulling the repository…"
                    : "Pull a repo to browse its files."}
                </div>
              ) : treeQ.isLoading ? (
                <LoadingState variant="list" rows={8} />
              ) : treeQ.isError ? (
                <ErrorState onRetry={() => treeQ.refetch()} />
              ) : (
                <>
                  {treeQ.data?.truncated && (
                    <p className="text-muted-foreground border-b px-3 py-1.5 text-[10px]">
                      Large repo — showing the first {treeQ.data.paths.length} files.
                    </p>
                  )}
                  <RepoFileTree
                    paths={treeQ.data?.paths ?? []}
                    selectedPath={selectedFile ?? undefined}
                    onSelect={setSelectedFile}
                    changes={changesMap}
                  />
                </>
              )}
            </div>
          ) : (
            <div className="min-h-0 flex-1 overflow-auto">
              {devPrs.length === 0 && !devPrsQ.isLoading && !devPrsQ.isError ? (
                <p className="text-muted-foreground p-3 text-xs">No pull requests yet.</p>
              ) : (
                <Tabs defaultValue="open" className="flex min-h-0 flex-1 flex-col">
                  <TabsList className="mx-2 mt-2 grid h-8 grid-cols-3 p-0.5 text-xs">
                    <TabsTrigger value="open" className="h-7">
                      Open <span className="text-muted-foreground ml-1">{buckets.open.length}</span>
                    </TabsTrigger>
                    <TabsTrigger value="review" className="h-7">
                      Review <span className="text-muted-foreground ml-1">{buckets.review.length}</span>
                    </TabsTrigger>
                    <TabsTrigger value="merged" className="h-7">
                      Merged <span className="text-muted-foreground ml-1">{buckets.merged.length}</span>
                    </TabsTrigger>
                  </TabsList>
                  <div className="flex-1 overflow-auto p-2">
                    {devPrsQ.isLoading ? (
                      <LoadingState variant="list" rows={3} />
                    ) : devPrsQ.isError ? (
                      <ErrorState onRetry={() => devPrsQ.refetch()} />
                    ) : (
                      <>
                        <TabsContent value="open" className="mt-0">
                          <DevPrBucket items={buckets.open} />
                        </TabsContent>
                        <TabsContent value="review" className="mt-0">
                          <DevPrBucket items={buckets.review} />
                        </TabsContent>
                        <TabsContent value="merged" className="mt-0">
                          <DevPrBucket items={buckets.merged} />
                        </TabsContent>
                      </>
                    )}
                  </div>
                </Tabs>
              )}
            </div>
          )}
        </aside>

        {/* Main pane — file viewer */}
        <main className="flex min-h-0 flex-col overflow-hidden">
          {selectedFile ? (
            <>
              <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
                <span className="flex min-w-0 items-center gap-2">
                  <FileCode2 className="text-muted-foreground size-4 shrink-0" aria-hidden />
                  <span className="truncate font-mono text-xs">{selectedFile}</span>
                </span>
                {fileQ.data && (
                  <span className="text-muted-foreground shrink-0 font-mono text-[10px]">
                    {formatBytes(fileQ.data.size)}
                    {fileQ.data.truncated && " · truncated"}
                  </span>
                )}
              </div>
              <div className="min-h-0 flex-1 overflow-hidden">
                {fileQ.isLoading ? (
                  <LoadingState variant="card" />
                ) : fileQ.isError ? (
                  <ErrorState onRetry={() => fileQ.refetch()} />
                ) : fileQ.data?.binary ? (
                  <EmptyState
                    icon={FileCode2}
                    title="Binary file"
                    description="This file can't be displayed as text."
                    variant="plain"
                    className="mt-10"
                  />
                ) : (
                  <CodeViewer
                    content={fileQ.data?.content ?? ""}
                    filename={selectedFile}
                    highlightLines={changedLinesQ.data?.added_lines}
                  />
                )}
              </div>
            </>
          ) : (
            <div className="flex-1 overflow-auto">
              <div className="mx-auto max-w-2xl px-4 py-10 md:px-6">
                <EmptyState
                  icon={ready ? FileCode2 : GitBranch}
                  title={ready ? "Select a file to view it" : "No repository pulled"}
                  description={
                    ready
                      ? "Browse the repository in the Explorer and pick a file to see its contents."
                      : "Pull a repo and ask the Dev agent to make a change — its files and PRs appear here."
                  }
                  variant="plain"
                />
              </div>
            </div>
          )}
        </main>
      </div>

      <RepoPickerDialog
        open={repoPickerOpen}
        onOpenChange={setRepoPickerOpen}
        projectId={projectId}
        onPulled={() => {
          setSelectedFile(null);
          setLeftTab("files");
          // A fresh pull is a clean checkout — hard-clear prior tree/changes/file
          // caches so old change decorations + line highlights don't linger.
          queryClient.removeQueries({ queryKey: ["dev-workspace", "tree", projectId] });
          queryClient.removeQueries({ queryKey: ["dev-workspace", "changes", projectId] });
          queryClient.removeQueries({ queryKey: ["dev-workspace", "file", projectId] });
          queryClient.removeQueries({ queryKey: ["dev-workspace", "changed-lines", projectId] });
          queryClient.invalidateQueries({ queryKey: qk.devWorkspace.workspace(projectId) });
          queryClient.invalidateQueries({ queryKey: qk.devWorkspace.prs(projectId) });
        }}
      />

      <AgentChatDrawer
        open={chatOpen}
        onOpenChange={setChatOpen}
        context={{ page: "Development" }}
        messages={chat.messages}
        onSend={chat.send}
        busy={chat.busy}
        sessions={chat.sessions}
        activeSessionId={chat.sessionId}
        onSelectSession={chat.selectSession}
        onNewChat={chat.newChat}
        attachments={chat.attachments}
        onAttachFiles={chat.attachFiles}
        onRemoveAttachment={chat.removeAttachment}
        starterSuggestions={[
          "Add a GET /health endpoint that returns 200",
          "Summarize this repository's structure and key modules",
          "Add unit tests for the most critical untested function",
          "Open a PR that fixes the lint errors in this branch",
        ]}
        disabledReason={
          workspaceQ.isLoading
            ? undefined
            : ready
              ? undefined
              : "No code has been pulled — pull a repo first."
        }
      />
    </div>
  );
}

// ───────── helpers ─────────

function SegBtn({
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
      aria-pressed={active}
      className={cn(
        "inline-flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors",
        "focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1",
        active ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/50",
      )}
    >
      <Icon className="size-3.5" aria-hidden />
      {children}
    </button>
  );
}

function DevPrBucket({ items }: { items: DevPr[] }) {
  if (items.length === 0) {
    return <p className="text-muted-foreground p-3 text-xs">Nothing here.</p>;
  }
  return (
    <ul className="flex flex-col gap-1">
      {items.map((pr) => (
        <li key={pr.id}>
          <a
            href={pr.url}
            target="_blank"
            rel="noreferrer noopener"
            className={cn(
              "flex w-full flex-col items-start gap-1 rounded-md border p-2 text-left transition-colors",
              "hover:bg-accent border-transparent",
              "focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1",
            )}
          >
            <div className="flex w-full items-center gap-2">
              <GitPullRequest className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{pr.title}</span>
              <ExternalLink className="text-muted-foreground size-3 shrink-0" aria-hidden />
            </div>
            <div className="text-muted-foreground font-mono text-[10px]">{pr.branch}</div>
            <StatusBadge status={pr.status} className="mt-0.5" />
          </a>
        </li>
      ))}
    </ul>
  );
}
