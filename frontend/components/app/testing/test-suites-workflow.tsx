"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Download, Eye, EyeOff, FileSpreadsheet, GitBranch, Loader2, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Pill } from "@/components/app/report-primitives";
import { SuiteCasesView } from "@/components/app/testing/suite-cases-view";
import { SuiteJobProgress } from "@/components/app/testing/suite-job-progress";
import { SuiteRunPanel } from "@/components/app/testing/suite-run-panel";
import type { UseRaiseForApprovalResult } from "@/hooks/use-raise-for-approval";
import { qk } from "@/lib/api/query-keys";
import {
  generateSuites, isActive, latestReports, latestSuites, listSuiteJobs, RUN_JOB_KIND, runSuite, SUITE_KINDS, SUITE_LABEL,
  suitesKeys, type SuiteJob, type SuiteKind, type SuiteTarget,
} from "@/lib/api/testing-suites";
import { approvalState } from "@/lib/documents/report-document";
import type { Artifact, ProjectId } from "@/lib/schemas";
import { cn } from "@/lib/utils";

const KIND_BLURB: Record<SuiteKind, string> = {
  unit: "Functions, services and route handlers tested in isolation — run in the repository.",
  functional: "User journeys step by step — run in a browser against the running app.",
  api: "Every endpoint's requests and responses — run against the running app.",
};

/**
 * The Testing page's flow: 1 · generate the test cases, review and approve them;
 * 2 · run the unit tests; 3 · run the functional and API tests against the app.
 *
 * Each step's documents are the project's Testing documents — the same rows as the panel
 * beside it — so an approval anywhere shows here, and a download is the stored workbook.
 */
export function TestSuitesWorkflow({ projectId, target, onSelectTarget, offeringId, documents, approvals, children }: {
  projectId: ProjectId;
  target: SuiteTarget | null;
  onSelectTarget: () => void;
  offeringId?: string;
  documents: readonly Artifact[] | null | undefined;
  approvals: UseRaiseForApprovalResult;
  /** Later steps (runs), rendered beneath step 1. */
  children?: React.ReactNode;
}) {
  const queryClient = useQueryClient();
  const [kinds, setKinds] = React.useState<SuiteKind[]>(SUITE_KINDS);
  const [open, setOpen] = React.useState<SuiteKind | null>(null);

  const jobsQ = useQuery({
    queryKey: suitesKeys.jobs(projectId),
    queryFn: () => listSuiteJobs(projectId),
    refetchInterval: (q) => (q.state.data?.jobs.some(isActive) ? 2000 : false),
  });
  const jobs = React.useMemo(() => jobsQ.data?.jobs ?? [], [jobsQ.data]);
  const generation: SuiteJob | null = jobs.find((j) => j.kind === "generate") ?? null;
  const lastRun = (k: SuiteKind): SuiteJob | null => jobs.find((j) => j.kind === RUN_JOB_KIND[k]) ?? null;

  // Work that just finished filed documents: refresh the lists that show them.
  const activeIds = React.useRef<Set<string>>(new Set());
  React.useEffect(() => {
    const now = new Set(jobs.filter(isActive).map((j) => j.id));
    const finished = [...activeIds.current].filter((id) => !now.has(id));
    if (finished.length) {
      void queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(projectId) });
      for (const id of finished) {
        const job = jobs.find((j) => j.id === id);
        if (job?.status !== "succeeded") continue;
        if (job.kind === "generate") {
          const n = job.result.documents.length;
          toast.success(`${n} test case document${n === 1 ? "" : "s"} filed as draft${n === 1 ? "" : "s"}`);
        } else {
          toast.success(`${String((job.result as { verdict?: string }).verdict ?? "Run")} — report filed as a draft`);
        }
      }
    }
    activeIds.current = now;
  }, [jobs, projectId, queryClient]);

  // The running app's address, remembered on this device for the next run.
  const urlKey = `testing:base-url:${projectId}`;
  const [baseUrl, setBaseUrl] = React.useState("");
  const [showBrowser, setShowBrowser] = React.useState(true);
  React.useEffect(() => {
    try { setBaseUrl(window.localStorage.getItem(urlKey) ?? ""); } catch { /* storage unavailable */ }
  }, [urlKey]);
  const rememberUrl = (value: string) => {
    setBaseUrl(value);
    try { window.localStorage.setItem(urlKey, value); } catch { /* storage unavailable */ }
  };
  const urlOk = /^https?:\/\/[^\s/]+/.test(baseUrl.trim());

  const run = useMutation({
    mutationFn: ({ kind, doc }: { kind: SuiteKind; doc: string }) => runSuite(projectId, doc, {
      ...(kind === "unit" ? {} : { base_url: baseUrl.trim() }),
      ...(kind === "functional" ? { headless: !showBrowser } : {}),
      ...(offeringId ? { offering_id: offeringId } : {}),
    }),
    onSuccess: () => void jobsQ.refetch(),
    onError: (e: Error, vars) => toast.error(`Could not start the ${SUITE_LABEL[vars.kind].toLowerCase()} run`, { description: e.message }),
  });
  const reports = latestReports(documents);

  const generate = useMutation({
    mutationFn: () => generateSuites(projectId, { target: target!, kinds, ...(offeringId ? { offering_id: offeringId } : {}) }),
    onSuccess: () => void jobsQ.refetch(),
    onError: (e: Error) => toast.error("Could not start generating test cases", { description: e.message }),
  });

  const suites = latestSuites(documents);
  const generating = isActive(generation) || generate.isPending;

  return (
    <div className="mx-auto max-w-5xl space-y-8 p-4 md:p-6">
      <section aria-labelledby="step-cases" className="space-y-4">
        <StepHeader n={1} id="step-cases" title="Test cases"
          description="Write unit, functional and API test cases from the branch, the approved BRD and design, and the code. Each is an Excel workbook you can download, review, edit and raise for approval." />

        <div className="bg-surface-1 flex flex-wrap items-center gap-x-5 gap-y-3 rounded-lg border p-3">
          {target ? (
            <span className="inline-flex min-w-0 items-center gap-1.5 text-sm">
              <GitBranch className="text-muted-foreground size-4 shrink-0" aria-hidden />
              <span className="truncate font-mono text-xs">{target.repo} @ {target.branch}</span>
            </span>
          ) : (
            <Button variant="outline" size="sm" onClick={onSelectTarget}>
              <GitBranch className="size-4" aria-hidden />Select a branch
            </Button>
          )}
          <fieldset className="flex flex-wrap items-center gap-4">
            <legend className="sr-only">Test types to generate</legend>
            {SUITE_KINDS.map((k) => (
              <label key={k} className="inline-flex cursor-pointer items-center gap-2 text-sm">
                <Checkbox
                  checked={kinds.includes(k)}
                  onCheckedChange={(v) => setKinds((cur) => (v ? SUITE_KINDS.filter((x) => x === k || cur.includes(x)) : cur.filter((x) => x !== k)))}
                  aria-label={`${SUITE_LABEL[k]} test cases`}
                />
                {SUITE_LABEL[k]}
              </label>
            ))}
          </fieldset>
          <Button className="ml-auto" onClick={() => generate.mutate()} disabled={!target || kinds.length === 0 || generating}>
            {generating ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Sparkles className="size-4" aria-hidden />}
            {generating ? "Generating…" : suites.unit || suites.functional || suites.api ? "Generate again" : "Generate test cases"}
          </Button>
        </div>

        {generation && (isActive(generation) || generation.status !== "succeeded" || isRecent(generation)) && (
          <SuiteJobProgress job={generation} title="Generating test cases" />
        )}

        <div className="grid gap-3 md:grid-cols-3">
          {SUITE_KINDS.map((k) => {
            const doc = suites[k];
            const state = doc ? approvalState(doc) : null;
            return (
              <article key={k} className={cn("flex flex-col gap-2 rounded-lg border p-3", open === k && "border-brand-bright/50")}>
                <header className="flex items-center gap-2">
                  <FileSpreadsheet className="text-brand-bright size-4" aria-hidden />
                  <h3 className="text-sm font-semibold">{SUITE_LABEL[k]} test cases</h3>
                </header>
                <p className="text-muted-foreground text-xs">{KIND_BLURB[k]}</p>
                {doc && state ? (
                  <>
                    <p className="truncate font-mono text-[11px]" title={doc.title}>{doc.title}</p>
                    <p className="text-muted-foreground text-[11px]">
                      {new Date(doc.createdAt).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
                    </p>
                    <Pill tone={state.tone} className="w-fit">{state.label}</Pill>
                    <div className="mt-auto flex flex-wrap gap-1.5 pt-1">
                      <Button size="sm" variant="outline" className="h-7 gap-1 text-xs" onClick={() => setOpen(open === k ? null : k)}
                        aria-expanded={open === k}>
                        {open === k ? <EyeOff className="size-3.5" aria-hidden /> : <Eye className="size-3.5" aria-hidden />}
                        {open === k ? "Hide cases" : "View cases"}
                      </Button>
                      {doc.downloadUrl && (
                        <Button asChild size="sm" variant="outline" className="h-7 gap-1 text-xs">
                          <a href={doc.downloadUrl} download><Download className="size-3.5" aria-hidden />Excel</a>
                        </Button>
                      )}
                      {doc.status === "draft" && approvals.mayRaise(doc.stage) && (
                        <Button size="sm" className="h-7 text-xs" disabled={approvals.raisingId === doc.id} onClick={() => approvals.raise(doc)}>
                          {approvals.raisingId === doc.id && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
                          Raise for approval
                        </Button>
                      )}
                    </div>
                  </>
                ) : (
                  <p className="text-muted-foreground mt-auto text-xs italic">Not generated yet.</p>
                )}
              </article>
            );
          })}
        </div>

        {open && suites[open] && (
          <div className="space-y-2">
            <h3 className="text-sm font-semibold">{SUITE_LABEL[open]} test cases</h3>
            <SuiteCasesView projectId={projectId} documentId={suites[open]!.id} kind={open} />
          </div>
        )}
      </section>

      <section aria-labelledby="step-unit" className="space-y-4">
        <StepHeader n={2} id="step-unit" title="Unit tests"
          description="Write a test for every unit case, run them in the repository, and file a report of each case passed or failed." />
        <SuiteRunPanel kind="unit" suite={suites.unit} job={lastRun("unit")} report={reports.unit} approvals={approvals}
          canRun={!!suites.unit} running={run.isPending && run.variables?.kind === "unit"}
          onRun={() => suites.unit && run.mutate({ kind: "unit", doc: suites.unit.id })} />
      </section>

      <section aria-labelledby="step-app" className="space-y-4">
        <StepHeader n={3} id="step-app" title="Functional and API tests"
          description="Run against the running application: the functional cases step by step in a browser, the API cases as requests to its endpoints." />
        <div className="bg-surface-1 flex flex-wrap items-end gap-x-5 gap-y-3 rounded-lg border p-3">
          <div className="min-w-64 flex-1 space-y-1">
            <Label htmlFor="testing-base-url" className="text-xs">Application URL</Label>
            <Input id="testing-base-url" type="url" placeholder="http://localhost:8080" value={baseUrl}
              onChange={(e) => rememberUrl(e.target.value)} className="h-9 font-mono text-sm" />
          </div>
          <label className="inline-flex cursor-pointer items-center gap-2 pb-2 text-sm">
            <Checkbox checked={showBrowser} onCheckedChange={(v) => setShowBrowser(v === true)} aria-label="Show the browser while functional tests run" />
            Show the browser
          </label>
        </div>
        <div className="grid gap-4">
          {(["functional", "api"] as const).map((k) => (
            <SuiteRunPanel key={k} kind={k} suite={suites[k]} job={lastRun(k)} report={reports[k]} approvals={approvals}
              canRun={!!suites[k] && urlOk}
              blockedReason={suites[k] && !urlOk ? "Enter the running application's URL above." : undefined}
              running={run.isPending && run.variables?.kind === k}
              onRun={() => suites[k] && run.mutate({ kind: k, doc: suites[k]!.id })} />
          ))}
        </div>
      </section>

      {children}
    </div>
  );
}

function isRecent(job: SuiteJob): boolean {
  return !!job.finished_at && Date.now() - Date.parse(job.finished_at) < 10 * 60_000;
}

export function StepHeader({ n, id, title, description }: { n: number; id: string; title: string; description: string }) {
  return (
    <header className="flex gap-3">
      <span className="bg-brand-bright/10 text-brand-bright flex size-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold">{n}</span>
      <div>
        <h2 id={id} className="text-base font-semibold">{title}</h2>
        <p className="text-muted-foreground text-sm">{description}</p>
      </div>
    </header>
  );
}
