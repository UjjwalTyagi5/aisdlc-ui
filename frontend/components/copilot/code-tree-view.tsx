"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { FileCode2, GitBranch } from "lucide-react";

import { cn } from "@/lib/utils";
import { API_BASE } from "@/lib/api/client";
import { WorkspaceTree, WorkspaceFile, WorkspaceChanges, FileChangedLines } from "@/lib/schemas";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { RepoFileTree, type ChangeStatus } from "@/components/app/repo-file-tree";
import { CodeViewer } from "@/components/app/code-viewer";

export interface CodeTreeViewProps {
  runId: string;
  /**
   * Which stage's files to browse. `"development"` (the default, when unset)
   * hits the live pulled-repo workspace proxies and keeps the existing
   * changed-lines diff highlighting; any other stage value hits the
   * read-only `/stage-files/{stage}/*` proxies for that stage's generated
   * output dir, with no diff highlighting (nothing to diff against).
   */
  source?: string;
  className?: string;
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

async function fetchJson<T>(url: string, parse: (raw: unknown) => T): Promise<T> {
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error(`request failed (${res.status})`);
  return parse(await res.json());
}

/**
 * Live file-tree viewer for the Copilot Artifacts panel. Renders a VS
 * Code-style `RepoFileTree` on the left and a syntax-highlighted `CodeViewer`
 * on the right. Defaults to the pulled Development workspace (`/runs/{id}/
 * workspace/*`); passing `source` to any other stage instead browses that
 * stage's generated-output dir via `/runs/{id}/stage-files/{source}/*` — the
 * same viewer, a read-only tree, no diff highlighting.
 *
 * While the content is still being produced (`ready: false`), the tree
 * endpoint is polled every 5s and a guard state is shown; polling stops once
 * ready. All fetch/parse failures fail soft into inline guard states so the
 * panel never crashes.
 */
export function CodeTreeView({ runId, source, className }: CodeTreeViewProps) {
  const [selectedFile, setSelectedFile] = React.useState<string | null>(null);

  // Development (default/unset) reads the live pulled-repo workspace, which
  // also supports the changes/changed-lines diff endpoints. Any other stage
  // reads that stage's generated-output dir — a plain read-only file tree,
  // no diff to highlight against.
  const isDev = !source || source === "development";
  const base = isDev
    ? `${API_BASE}/runs/${encodeURIComponent(runId)}/workspace`
    : `${API_BASE}/runs/${encodeURIComponent(runId)}/stage-files/${encodeURIComponent(source)}`;

  const treeQ = useQuery({
    queryKey: ["copilot-workspace", "tree", runId, source ?? "development"],
    queryFn: () => fetchJson(`${base}/tree`, (raw) => WorkspaceTree.parse(raw)),
    enabled: !!runId,
    // Poll while the repo is still being pulled; stop once the tree is ready.
    refetchInterval: (query) => (query.state.data?.ready ? false : 5_000),
  });

  const ready = treeQ.data?.ready ?? false;

  // Change decorations mirror the standalone Development page: a `path → status`
  // map for `RepoFileTree` + per-file `added_lines` for `CodeViewer`. Polled
  // every 5s while the repo is being edited so the agent's edits surface live;
  // fails soft (no data → no decorations, unchanged behavior). Dev-only — other
  // stages have nothing to diff against.
  const changesQ = useQuery({
    queryKey: ["copilot-workspace", "changes", runId],
    queryFn: () =>
      fetchJson(`${API_BASE}/runs/${encodeURIComponent(runId)}/workspace/changes`, (raw) =>
        WorkspaceChanges.parse(raw),
      ),
    enabled: isDev && ready && !!runId,
    refetchInterval: 5_000,
  });

  const changesMap = React.useMemo(() => {
    const m = new Map<string, ChangeStatus>();
    for (const f of changesQ.data?.files ?? []) m.set(f.path, f.status);
    return m;
  }, [changesQ.data]);

  const fileQ = useQuery({
    queryKey: ["copilot-workspace", "file", runId, source ?? "development", selectedFile],
    queryFn: () =>
      fetchJson(`${base}/file?path=${encodeURIComponent(selectedFile!)}`, (raw) =>
        WorkspaceFile.parse(raw),
      ),
    enabled: ready && !!selectedFile,
    staleTime: 5 * 60_000,
  });

  const changedLinesQ = useQuery({
    queryKey: ["copilot-workspace", "changed-lines", runId, selectedFile],
    queryFn: () =>
      fetchJson(
        `${API_BASE}/runs/${encodeURIComponent(
          runId,
        )}/workspace/file/changed-lines?path=${encodeURIComponent(selectedFile!)}`,
        (raw) => FileChangedLines.parse(raw),
      ),
    enabled: isDev && ready && !!selectedFile && changesMap.has(selectedFile),
  });

  return (
    <div className={cn("grid h-full min-h-0 grid-cols-[minmax(180px,42%)_1fr]", className)}>
      {/* Left — file tree */}
      <div className="border-line-soft min-h-0 overflow-auto border-r">
        {treeQ.isLoading ? (
          <div className="p-2">
            <LoadingState variant="list" rows={7} />
          </div>
        ) : treeQ.isError ? (
          <div className="p-3">
            <ErrorState
              variant="plain"
              title="Couldn't load the code tree"
              onRetry={() => treeQ.refetch()}
            />
          </div>
        ) : !ready ? (
          <div className="text-muted-foreground p-4 text-center text-xs">
            <GitBranch className="mx-auto mb-2 size-5 opacity-60" aria-hidden />
            {isDev
              ? "Waiting for the Development agent to pull the repository…"
              : `Waiting for the ${source} agent to generate files…`}
          </div>
        ) : (
          <>
            {treeQ.data?.truncated && (
              <p className="text-muted-foreground border-line-soft border-b px-3 py-1.5 text-[10px]">
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

      {/* Right — file viewer */}
      <div className="flex min-h-0 flex-col overflow-hidden">
        {selectedFile ? (
          <>
            <div className="border-line-soft flex items-center justify-between gap-2 border-b px-3 py-2">
              <span className="flex min-w-0 items-center gap-2">
                <FileCode2 className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
                <span className="truncate font-mono text-[11px]">{selectedFile}</span>
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
                <div className="p-3">
                  <LoadingState variant="card" />
                </div>
              ) : fileQ.isError ? (
                <div className="p-3">
                  <ErrorState variant="plain" onRetry={() => fileQ.refetch()} />
                </div>
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
          <div className="flex min-h-0 flex-1 items-center justify-center p-6">
            <EmptyState
              icon={ready ? FileCode2 : GitBranch}
              title={ready ? "Select a file to view it" : "No files yet"}
              description={
                ready
                  ? "Pick a file from the tree to see its contents."
                  : isDev
                    ? "The Development agent's pulled repo will appear here."
                    : `The ${source} agent's generated files will appear here.`
              }
              variant="plain"
            />
          </div>
        )}
      </div>
    </div>
  );
}
