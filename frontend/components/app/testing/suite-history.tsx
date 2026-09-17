"use client";

import * as React from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Download, FileSpreadsheet, GitBranch, History, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { Pill, type PillTone } from "@/components/app/report-primitives";
import { when } from "@/components/app/testing/test-suites-workflow";
import {
  entryKinds, entryTarget, isActive, listHistory, runResult, SUITE_KINDS, SUITE_LABEL, suitesKeys,
  type HistoryEntry, type SuiteJob, type SuiteKind,
} from "@/lib/api/testing-suites";
import type { Artifact, ProjectId } from "@/lib/schemas";
import { cn } from "@/lib/utils";

const PAGE = 20;
const VERDICT_TONE: Record<string, PillTone> = { Passed: "success", Failed: "danger", Incomplete: "warning" };
const DOC_STATUS: Record<string, { label: string; tone: PillTone }> = {
  draft: { label: "Draft", tone: "neutral" },
  awaiting_approval: { label: "Awaiting approval", tone: "warning" },
  approved: { label: "Approved", tone: "success" },
  rejected: { label: "Rejected", tone: "danger" },
};

/**
 * Everything generated and run on this project's Testing page before: one entry per
 * generation, with every run of its test cases. Opening one puts it back on the page.
 */
export function SuiteHistory({ projectId, documents, openEntryId, onOpen }: {
  projectId: ProjectId;
  /** The project's documents; null while they load. */
  documents: readonly Artifact[] | null | undefined;
  openEntryId: string | null;
  onOpen: (entryId: string) => void;
}) {
  const q = useInfiniteQuery({
    queryKey: suitesKeys.history(projectId),
    initialPageParam: 0,
    queryFn: ({ pageParam }) => listHistory(projectId, { limit: PAGE, offset: pageParam }),
    getNextPageParam: (last, pages) => {
      const seen = pages.reduce((n, p) => n + p.entries.length, 0);
      return seen < last.total ? seen : undefined;
    },
    refetchInterval: (query) => (query.state.data?.pages.some((p) => p.entries.some((e) => e.active)) ? 4000 : false),
  });
  const docById = React.useMemo(() => new Map<string, Artifact>((documents ?? []).map((d) => [d.id, d])), [documents]);

  if (q.isLoading) return <div className="mx-auto max-w-5xl p-4 md:p-6"><LoadingState variant="card" /></div>;
  if (q.isError) {
    return (
      <div className="mx-auto max-w-5xl p-4 md:p-6">
        <ErrorState title="The testing history could not be loaded"
          description={q.error instanceof Error ? q.error.message : "Unknown error."} onRetry={() => q.refetch()} />
      </div>
    );
  }
  const entries = q.data?.pages.flatMap((p) => p.entries) ?? [];
  const total = q.data?.pages[0]?.total ?? 0;
  if (entries.length === 0) {
    return (
      <div className="mx-auto max-w-xl px-4 py-12">
        <EmptyState icon={History} variant="plain" title="No testing history yet"
          description="Test cases you generate, and every run of them, are listed here to open again." />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-3 p-4 md:p-6">
      <p className="text-muted-foreground text-xs">
        {total} {total === 1 ? "entry" : "entries"} · latest activity first
      </p>
      <ol className="space-y-3" aria-label="Testing history">
        {entries.map((e) => (
          <HistoryItem key={e.id} entry={e} docById={docById} loaded={documents != null}
            isOpen={e.id === openEntryId} onOpen={() => onOpen(e.id)} />
        ))}
      </ol>
      {q.hasNextPage && (
        <div className="flex justify-center">
          <Button variant="outline" size="sm" onClick={() => void q.fetchNextPage()} disabled={q.isFetchingNextPage}>
            {q.isFetchingNextPage && <Loader2 className="size-4 animate-spin" aria-hidden />}
            Show more
          </Button>
        </div>
      )}
    </div>
  );
}

function HistoryItem({ entry, docById, loaded, isOpen, onOpen }: {
  entry: HistoryEntry; docById: Map<string, Artifact>; loaded: boolean; isOpen: boolean; onOpen: () => void;
}) {
  const gen = entry.generation;
  const target = entryTarget(entry);
  const perKind = entryKinds(entry);
  const suites = SUITE_KINDS.filter((k) => perKind[k].documentId);
  const title = target ? `${target.repo} @ ${target.branch}` : "Run of an uploaded workbook";

  return (
    <li className={cn("space-y-3 rounded-lg border p-3", isOpen && "border-brand-bright/50")} aria-label={title}>
      <header className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1 space-y-0.5">
          <p className="inline-flex max-w-full items-center gap-1.5 text-sm font-medium">
            <GitBranch className="text-muted-foreground size-4 shrink-0" aria-hidden />
            <span className="truncate font-mono text-xs">{title}</span>
          </p>
          <p className="text-muted-foreground text-xs">
            {gen ? `Generated ${when(gen.created_at)}` : when(entry.runs[0]?.created_at)}
            {gen?.user_name ? ` by ${gen.user_name}` : ""}
          </p>
        </div>
        {gen && <JobStatus job={gen} done="Generated" />}
        {isOpen ? (
          <Pill tone="accent">Open on the page</Pill>
        ) : (
          <Button size="sm" variant="outline" className="h-8" onClick={onOpen}>Open</Button>
        )}
      </header>

      {gen && (gen.status === "failed" || gen.status === "interrupted") && gen.error && (
        <p className="text-destructive text-xs">{gen.error}</p>
      )}

      {suites.length > 0 && (
        <div className="flex flex-wrap gap-2" aria-label="Test cases">
          {suites.map((k) => {
            const info = perKind[k];
            const doc = docById.get(info.documentId!);
            const status = doc ? DOC_STATUS[doc.status] ?? { label: doc.status, tone: "neutral" as PillTone } : null;
            return (
              <span key={k} className="bg-surface-1 inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs">
                <FileSpreadsheet className="text-brand-bright size-3.5" aria-hidden />
                <span className="font-medium">{SUITE_LABEL[k]}</span>
                {info.cases != null && <span className="text-muted-foreground">{info.cases} cases</span>}
                {status ? <Pill tone={status.tone}>{status.label}</Pill>
                  : loaded ? <Pill tone="warning">Removed</Pill> : null}
              </span>
            );
          })}
        </div>
      )}

      {entry.runs.length > 0 ? (
        <ul className="divide-y rounded-md border" aria-label="Runs">
          {entry.runs.map((r) => <RunRow key={r.id} run={r} docById={docById} loaded={loaded} />)}
        </ul>
      ) : gen?.status === "succeeded" ? (
        <p className="text-muted-foreground text-xs italic">Not run yet.</p>
      ) : null}
    </li>
  );
}

function RunRow({ run, docById, loaded }: { run: SuiteJob; docById: Map<string, Artifact>; loaded: boolean }) {
  const kind = (run.params.suite_kind as SuiteKind | undefined) ?? null;
  const result = runResult(run);
  const t = result?.totals ?? {};
  const filed = run.result.documents[0];
  const report = filed ? docById.get(filed.artifact_id) : undefined;
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-xs">
      <span className="w-20 font-medium">{kind ? SUITE_LABEL[kind] : "Run"} tests</span>
      {result ? (
        <>
          <Pill tone={VERDICT_TONE[result.verdict] ?? "neutral"}>{result.verdict}</Pill>
          <span className="tabular-nums">
            {t.Passed ?? 0} of {result.rows.length} passed
            {(t.Failed ?? 0) > 0 && <span className="text-destructive"> · {t.Failed} failed</span>}
            {(t.Error ?? 0) > 0 && <span className="text-warning"> · {t.Error} could not run</span>}
            {(t["Not run"] ?? 0) > 0 && <span className="text-muted-foreground"> · {t["Not run"]} not run</span>}
          </span>
        </>
      ) : (
        <JobStatus job={run} done="Done" />
      )}
      <span className="text-muted-foreground min-w-0 flex-1 truncate">
        {when(run.created_at)}
        {run.user_name ? ` · ${run.user_name}` : ""}
        {typeof run.params.base_url === "string" && run.params.base_url ? ` · ${run.params.base_url}` : ""}
      </span>
      {(run.status === "failed" || run.status === "interrupted") && run.error && (
        <span className="text-destructive basis-full">{run.error}</span>
      )}
      {report?.downloadUrl ? (
        <Button asChild size="sm" variant="outline" className="h-7 gap-1 text-xs">
          <a href={report.downloadUrl} download><Download className="size-3.5" aria-hidden />Report</a>
        </Button>
      ) : filed && loaded ? (
        <span className="text-warning">Report removed</span>
      ) : null}
    </li>
  );
}

function JobStatus({ job, done }: { job: SuiteJob; done: string }) {
  if (isActive(job)) {
    return <Pill tone="info"><Loader2 className="size-3 animate-spin" aria-hidden />{job.kind === "generate" ? "Generating" : "Running"}</Pill>;
  }
  if (job.status === "succeeded") return <Pill tone="success">{done}</Pill>;
  return <Pill tone="danger">{job.status === "interrupted" ? "Interrupted" : "Failed"}</Pill>;
}
