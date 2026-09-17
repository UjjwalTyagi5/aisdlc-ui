"use client";

import * as React from "react";
import { Download, Loader2, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Callout, Pill, ReportTable, td, th, type PillTone } from "@/components/app/report-primitives";
import { SuiteJobProgress } from "@/components/app/testing/suite-job-progress";
import type { UseRaiseForApprovalResult } from "@/hooks/use-raise-for-approval";
import { isActive, runResult, SUITE_LABEL, type ResultRow, type SuiteJob, type SuiteKind } from "@/lib/api/testing-suites";
import { approvalState } from "@/lib/documents/report-document";
import type { Artifact } from "@/lib/schemas";
import { cn } from "@/lib/utils";

const STATUS_TONE: Record<ResultRow["status"], PillTone> = { Passed: "success", Failed: "danger", Error: "warning", "Not run": "neutral" };
const VERDICT_TONE: Record<string, PillTone> = { Passed: "success", Failed: "danger", Incomplete: "warning" };

/**
 * One kind of run: what it runs, the button, the work as it happens, every case's outcome, and
 * the report filed for it. The table is the run's own result — the same rows as the workbook.
 */
export function SuiteRunPanel({ kind, suite, job, report, reportRemoved, approvals, canRun, blockedReason, running, onRun }: {
  kind: SuiteKind;
  suite: Artifact | null;
  job: SuiteJob | null;
  report: Artifact | null;
  /** The name of the report this run filed, when it has since been removed from the project's documents. */
  reportRemoved?: string;
  approvals: UseRaiseForApprovalResult;
  canRun: boolean;
  /** Why the run cannot start yet, shown under the button. */
  blockedReason?: string;
  running: boolean;
  onRun: () => void;
}) {
  const label = SUITE_LABEL[kind];
  const result = runResult(job);
  const active = isActive(job) || running;
  const suiteState = suite ? approvalState(suite) : null;
  const reportState = report ? approvalState(report) : null;
  const t = result?.totals ?? {};

  return (
    <article className="space-y-3 rounded-lg border p-4" aria-label={`${label} test run`}>
      <header className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold">{label} tests</h3>
          {suite && suiteState ? (
            <p className="text-muted-foreground flex flex-wrap items-center gap-1.5 text-xs">
              Runs <span className="font-mono">{suite.title}</span>
              <Pill tone={suiteState.tone}>{suiteState.label}</Pill>
            </p>
          ) : (
            <p className="text-muted-foreground text-xs">Generate the {label.toLowerCase()} test cases first (step 1).</p>
          )}
        </div>
        <Button onClick={onRun} disabled={!canRun || active}>
          {active ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Play className="size-4" aria-hidden />}
          {active ? "Running…" : `Run ${label.toLowerCase()} tests`}
        </Button>
      </header>
      {blockedReason && !active && <p className="text-warning text-xs">{blockedReason}</p>}

      {job && (isActive(job) || job.status !== "succeeded") && <SuiteJobProgress job={job} title={`Running ${label.toLowerCase()} tests`} />}

      {result && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Pill tone={VERDICT_TONE[result.verdict] ?? "neutral"} className="px-2.5 py-1 text-xs">{result.verdict}</Pill>
            <Pill tone="success">{t.Passed ?? 0} passed</Pill>
            <Pill tone="danger">{t.Failed ?? 0} failed</Pill>
            <Pill tone="warning">{t.Error ?? 0} could not run</Pill>
            <Pill tone="neutral">{t["Not run"] ?? 0} not run</Pill>
            <span className="text-muted-foreground">
              {job?.finished_at ? new Date(job.finished_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : ""}
              {result.commit ? ` · commit ${result.commit.slice(0, 7)}` : ""}
              {result.target_url ? ` · ${result.target_url}` : ""}
            </span>
          </div>
          <ReportTable dense head={<><th className={cn(th, "w-20")}>ID</th><th className={th}>Test case</th><th className={cn(th, "w-24")}>Result</th><th className={th}>Details</th></>}>
            {result.rows.map((r) => (
              <tr key={r.id}>
                <td className={cn(td, "font-mono text-[11px] font-semibold")}>{r.id}</td>
                <td className={td}>
                  <p className="font-medium">{r.title}</p>
                  {r.subject && <p className="text-muted-foreground font-mono text-[11px]">{r.subject}</p>}
                </td>
                <td className={td}><Pill tone={STATUS_TONE[r.status]}>{r.status}</Pill></td>
                <td className={cn(td, "text-muted-foreground whitespace-pre-wrap text-xs")}>
                  {r.message || (r.status === "Passed" ? "—" : "")}
                  {r.evidence && r.status !== "Passed" && <span className="block font-mono text-[10px]">{r.evidence}</span>}
                </td>
              </tr>
            ))}
          </ReportTable>
        </div>
      )}

      {report && reportState && (
        <div className="bg-surface-1 flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-xs">
          <span className="font-medium">Report</span>
          <span className="truncate font-mono">{report.title}</span>
          <Pill tone={reportState.tone}>{reportState.label}</Pill>
          <span className="ml-auto flex gap-1.5">
            {report.downloadUrl && (
              <Button asChild size="sm" variant="outline" className="h-7 gap-1 text-xs">
                <a href={report.downloadUrl} download><Download className="size-3.5" aria-hidden />Excel</a>
              </Button>
            )}
            {report.status === "draft" && approvals.mayRaise(report.stage) && (
              <Button size="sm" className="h-7 text-xs" disabled={approvals.raisingId === report.id} onClick={() => approvals.raise(report)}>
                {approvals.raisingId === report.id && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
                Raise for approval
              </Button>
            )}
          </span>
        </div>
      )}
      {reportRemoved && !report && (
        <p className="text-warning text-xs">
          The report <span className="font-mono">{reportRemoved}</span> was removed from the project&apos;s documents.
        </p>
      )}
      {!result && !job && !report && suite && (
        <Callout>No {label.toLowerCase()} run yet.</Callout>
      )}
    </article>
  );
}
