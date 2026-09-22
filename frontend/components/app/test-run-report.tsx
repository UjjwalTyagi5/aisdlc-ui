"use client";

import * as React from "react";
import {
  AlertTriangle,
  Bug,
  CheckCircle2,
  ChevronDown,
  ClipboardList,
  FileCode2,
  FlaskConical,
  Gauge,
  Timer,
  XCircle,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { RunReport, RunVerdict, TestStatus } from "@/lib/api/testing";
import {
  Callout,
  FactStrip,
  MonoChip,
  PctBar,
  Pill,
  type PillTone,
  ReportHero,
  ReportSection,
  ReportTable,
  formatDuration,
  pctTone,
  td,
  th,
  toneForWord,
} from "@/components/app/report-primitives";

/**
 * The Testing agent's run, as a report.
 *
 * WHAT THE PAGE SHOWED BEFORE: the agent's closing prose in one bubble, with its
 * `##` and `**` on screen and the answer to "did it pass" in paragraph six — and,
 * on the run that prompted this, "Passed 2/2" while the generated suite had failed
 * to parse. This renders the run's own numbers (`run_report.json`) and says the
 * awkward thing first: the verdict is the title.
 */

const VERDICT: Record<RunVerdict, { title: (r: RunReport) => string; tone: PillTone; label: string; icon: React.ElementType }> = {
  passed: {
    title: (r) => `${r.execution.passed} of ${r.execution.total} tests passed`,
    tone: "success", label: "Passed", icon: CheckCircle2,
  },
  failed: {
    title: (r) => `${r.execution.failed + r.execution.errors} of ${r.execution.total} tests failed`,
    tone: "danger", label: "Failed", icon: XCircle,
  },
  partial: {
    title: (r) => `${r.execution.passed} of ${r.execution.total} tests passed — ${r.unrunnableSuites.length} generated ${r.unrunnableSuites.length === 1 ? "suite" : "suites"} could not run`,
    tone: "warning", label: "Partial", icon: AlertTriangle,
  },
  no_tests: { title: () => "No tests were executed", tone: "neutral", label: "No tests", icon: FlaskConical },
  error: { title: () => "The run did not complete", tone: "danger", label: "Error", icon: XCircle },
};

const STATUS_TONE: Record<TestStatus, PillTone> = { passed: "success", failed: "danger", error: "danger", skipped: "warning" };
const STATUS_LABEL: Record<TestStatus, string> = { passed: "Pass", failed: "Fail", error: "Error", skipped: "Skipped" };

const TYPE_LABEL: Record<string, string> = {
  unit: "Unit", functional: "Functional", api: "API", contract: "Contract", integration: "Integration",
  performance: "Performance", security: "Security", ui: "UI", e2e: "End-to-end", regression: "Regression",
};

function typeLabel(t: string) {
  return TYPE_LABEL[t] ?? t.charAt(0).toUpperCase() + t.slice(1);
}

function severityTone(s: string): PillTone {
  return toneForWord(s) ?? "warning";
}

/** One plain sentence under the verdict: what ran, where, and how it went. */
function runSentence(r: RunReport): string | undefined {
  const ex = r.execution;
  if (r.verdict === "error") return "The runner stopped before it could report results. See the run details and the agent's notes.";
  if (ex.total === 0) return undefined;
  const parts = [`${ex.passed} passed`];
  if (ex.failed) parts.push(`${ex.failed} failed`);
  if (ex.errors) parts.push(`${ex.errors} errored`);
  if (ex.skipped) parts.push(`${ex.skipped} skipped`);
  const where = [r.target.repo, r.target.branch].filter(Boolean).join(" @ ");
  const runner = r.framework ? ` with ${r.framework}` : "";
  const took = ex.durationMs ? ` in ${formatDuration(ex.durationMs)}` : "";
  return `${ex.total} ${ex.total === 1 ? "test" : "tests"} ran${where ? ` on ${where}` : ""}${runner}${took}: ${parts.join(", ")}.`;
}

export function TestRunReport({ report, actions, className }: {
  report: RunReport;
  /** Buttons for the band's corner — Download reports, Open tests PR. */
  actions?: React.ReactNode;
  className?: string;
}) {
  const v = VERDICT[report.verdict];
  const VerdictIcon = v.icon;
  const ex = report.execution;
  const cov = report.coverage;
  const types = report.testTypes.map(typeLabel).join(", ") || "Tests";
  const target = [report.target.repo, report.target.branch].filter(Boolean).join(" @ ");
  const tail = [types, target].filter(Boolean).join(" · ");
  const threshold = cov.thresholdPct ?? null;
  const covTone = pctTone(cov.linePct, threshold);
  const hasCoverage = cov.statements > 0 || cov.files.length > 0 || cov.linePct > 0;

  let n = 0;
  const next = () => ++n;

  return (
    <article className={cn("mx-auto max-w-5xl space-y-8 p-4 md:p-6", className)}>
      <header className="space-y-3">
        <ReportHero
          eyebrow="Test run report"
          eyebrowTail={tail}
          title={v.title(report)}
          subtitle={runSentence(report)}
          meta={[report.framework, report.runnerCommand].filter(Boolean).join(" · ") || undefined}
          aside={
            <>
              <Pill tone={v.tone} className="px-2.5 py-1 text-xs">
                <VerdictIcon className="size-3.5" aria-hidden />
                {v.label}
              </Pill>
              {actions}
            </>
          }
        />
        <FactStrip
          facts={[
            {
              label: "Tests", icon: FlaskConical,
              value: `${ex.passed} / ${ex.total}`,
              hint: ex.failed + ex.errors > 0 ? `${ex.failed + ex.errors} failed` : ex.skipped > 0 ? `${ex.skipped} skipped` : "all passed",
              tone: ex.total === 0 ? "default" : ex.failed + ex.errors > 0 ? "danger" : "success",
            },
            hasCoverage
              ? {
                  label: "Line coverage", icon: Gauge,
                  value: `${cov.linePct.toFixed(1)}%`,
                  hint: threshold ? `threshold ${threshold}%` : `${cov.statements - cov.missed} / ${cov.statements} lines`,
                  tone: covTone === "success" ? "success" : covTone === "warning" ? "warning" : "danger",
                }
              : { label: "Line coverage", icon: Gauge, value: "—", hint: "not measured" },
            typeof cov.applicationPct === "number"
              ? {
                  label: "App source", icon: FileCode2,
                  value: `${cov.applicationPct.toFixed(1)}%`,
                  hint: "excluding views & config",
                }
              : { label: "App source", icon: FileCode2, value: "" },
            { label: "Duration", icon: Timer, value: formatDuration(ex.durationMs) },
            { label: "Test cases", icon: ClipboardList, value: report.testCases.length || "—", hint: report.testCases.length ? "designed by the agent" : undefined },
            {
              label: "Defects", icon: Bug,
              value: report.defects.length,
              tone: report.defects.length ? "danger" : "success",
              hint: report.defects.length ? `${report.defects.filter((d) => ["high", "critical"].includes(d.severity)).length} high` : "none raised",
            },
          ]}
        />
      </header>

      {report.unrunnableSuites.length > 0 && (
        <Callout tone="danger" title="Generated tests did not run">
          {report.unrunnableSuites.map((s) => (
            <p key={s.file} className="mt-1 first:mt-0">
              <MonoChip className="mr-2">{s.file}</MonoChip>
              {s.reason}
            </p>
          ))}
          <p className="text-muted-foreground mt-2 text-xs">
            The counts above are the tests that did run — the repository&apos;s own. Fix the generated file (or ask the
            agent to regenerate it) and run again.
          </p>
        </Callout>
      )}

      {/* ── results ── */}
      <ReportSection n={next()} title="Results" id="run-results"
        aside={report.tests.length ? `${report.tests.length} tests · ${report.framework || "runner"}` : undefined}>
        {report.tests.length === 0 ? (
          <Callout tone="neutral">
            {report.verdict === "error"
              ? "The runner did not produce a results file. The run details below carry what it reported."
              : "No individual test results were recorded for this run."}
          </Callout>
        ) : (
          <ResultsTable tests={report.tests} />
        )}
      </ReportSection>

      {/* ── coverage ── */}
      {hasCoverage && (
        <ReportSection n={next()} title="Coverage" id="run-coverage"
          aside={threshold ? `threshold ${threshold}%` : undefined}>
          <div className="bg-card space-y-2 rounded-xl border px-4 py-3">
            <div className="flex items-baseline justify-between">
              <span className="text-sm font-medium">All measured files</span>
              <span className={cn("font-display text-xl font-semibold tabular-nums",
                covTone === "success" ? "text-success" : covTone === "warning" ? "text-amber-800 dark:text-amber-300" : "text-red-700 dark:text-red-400")}>
                {cov.linePct.toFixed(1)}%
              </span>
            </div>
            <PctBar pct={cov.linePct} threshold={threshold} />
            <p className="text-muted-foreground text-xs">
              {cov.statements - cov.missed} of {cov.statements} lines
              {typeof cov.branchPct === "number" ? ` · branches ${cov.branchPct.toFixed(1)}%` : ""}
              {typeof cov.applicationPct === "number" ? ` · application source ${cov.applicationPct.toFixed(1)}%` : ""}
            </p>
          </div>
          {cov.files.length > 0 && <CoverageFiles files={cov.files} threshold={threshold} />}
        </ReportSection>
      )}

      {/* ── designed test cases ── */}
      {report.testCases.length > 0 && (
        <ReportSection n={next()} title="Test cases" id="run-cases" aside={`${report.testCases.length} designed`}>
          <TestCases cases={report.testCases} />
        </ReportSection>
      )}

      {/* ── defects ── */}
      <ReportSection n={next()} title="Defects" id="run-defects" aside={report.defects.length ? `${report.defects.length} raised` : undefined}>
        {report.defects.length === 0 ? (
          <Callout tone="success">No defects were raised by this run.</Callout>
        ) : (
          <ul className="space-y-2">
            {report.defects.map((d, i) => (
              <li key={`${d.id}-${i}`} className="bg-card rounded-xl border px-4 py-3 shadow-[inset_3px_0_0_0_var(--destructive)]">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[11px] font-semibold text-red-700 dark:text-red-400">{d.id || `DEF-${i + 1}`}</span>
                  <Pill tone={severityTone(d.severity)}>{d.severity}</Pill>
                  <span className="text-sm font-medium">{d.summary}</span>
                </div>
                {d.detail && (
                  <pre className="bg-muted/60 mt-2 max-h-40 overflow-auto rounded-md px-3 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">{d.detail}</pre>
                )}
              </li>
            ))}
          </ul>
        )}
      </ReportSection>

      {/* ── run details ── */}
      <ReportSection n={next()} title="Run details" id="run-details">
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
          <Detail label="Target">{[report.target.project, target].filter(Boolean).join(" · ") || "—"}</Detail>
          <Detail label="Language · framework">{[report.language, report.framework].filter(Boolean).join(" · ") || "—"}</Detail>
          <Detail label="Runner">{report.runnerCommand ? <MonoChip>{report.runnerCommand}</MonoChip> : "—"}</Detail>
          <Detail label="Lint">
            {typeof report.lintExit === "number"
              ? <Pill tone={report.lintExit === 0 ? "success" : "warning"}>{report.lintExit === 0 ? "clean" : `exit ${report.lintExit}`}</Pill>
              : "not run"}
          </Detail>
          <Detail label="Generated test files" wide>
            {report.generatedFiles.length ? (
              <div className="flex flex-wrap gap-1.5">{report.generatedFiles.map((f) => <MonoChip key={f}>{f}</MonoChip>)}</div>
            ) : "none"}
          </Detail>
          <Detail label="Report files" wide>
            {report.artifactFiles.length ? (
              <div className="flex flex-wrap gap-1.5">{report.artifactFiles.map((f) => <MonoChip key={f}>{f}</MonoChip>)}</div>
            ) : "none"}
          </Detail>
        </dl>
      </ReportSection>
    </article>
  );
}

function Detail({ label, children, wide }: { label: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className={cn(wide && "sm:col-span-2")}>
      <dt className="text-muted-foreground text-[10.5px] font-semibold tracking-wide uppercase">{label}</dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}

/* ── results table ──────────────────────────────────────────────────────── */

function ResultsTable({ tests }: { tests: RunReport["tests"] }) {
  const [showAll, setShowAll] = React.useState(false);
  // Failures first, then the rest in the order they ran.
  const ordered = React.useMemo(() => {
    const rank: Record<TestStatus, number> = { failed: 0, error: 0, skipped: 1, passed: 2 };
    return [...tests].map((t, i) => ({ t, i })).sort((a, b) => rank[a.t.status] - rank[b.t.status] || a.i - b.i).map((x) => x.t);
  }, [tests]);
  const LIMIT = 25;
  const rows = showAll ? ordered : ordered.slice(0, LIMIT);
  return (
    <div className="space-y-2">
      <ReportTable head={<><th className={cn(th, "w-24")}>Status</th><th className={th}>Test</th><th className={cn(th, "w-48")}>Suite</th><th className={cn(th, "w-20 text-right")}>Time</th></>} dense>
        {rows.map((t, i) => (
          <React.Fragment key={`${t.suite}::${t.name}::${i}`}>
            <tr>
              <td className={td}><Pill tone={STATUS_TONE[t.status]} dot>{STATUS_LABEL[t.status]}</Pill></td>
              <td className={cn(td, "font-medium")}>{t.name}</td>
              <td className={cn(td, "text-muted-foreground truncate font-mono text-[11px]")} title={t.suite}>{t.suite || "—"}</td>
              <td className={cn(td, "text-muted-foreground text-right tabular-nums")}>{formatDuration(t.durationMs)}</td>
            </tr>
            {t.message && t.status !== "passed" && (
              <tr className="!border-t-0">
                <td />
                <td colSpan={3} className="px-3 pb-2">
                  <pre className="bg-destructive/5 rounded-md px-3 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-red-800 dark:text-red-300">{t.message}</pre>
                </td>
              </tr>
            )}
          </React.Fragment>
        ))}
      </ReportTable>
      {ordered.length > LIMIT && (
        <button type="button" onClick={() => setShowAll((s) => !s)}
          className="text-primary inline-flex items-center gap-1 text-xs font-medium hover:underline">
          <ChevronDown className={cn("size-3.5 transition-transform", showAll && "rotate-180")} aria-hidden />
          {showAll ? "Show fewer" : `Show all ${ordered.length} tests`}
        </button>
      )}
    </div>
  );
}

/* ── per-file coverage ──────────────────────────────────────────────────── */

function CoverageFiles({ files, threshold }: { files: RunReport["coverage"]["files"]; threshold: number | null }) {
  const [showAll, setShowAll] = React.useState(false);
  const LIMIT = 12;
  const rows = showAll ? files : files.slice(0, LIMIT);
  return (
    <div className="space-y-2">
      <p className="text-muted-foreground text-xs">Least covered first.</p>
      <ul className="bg-card divide-y rounded-xl border">
        {rows.map((f) => {
          const tone = pctTone(f.pct, threshold);
          return (
            <li key={f.path} className="grid grid-cols-[minmax(0,1fr)_7rem_3.5rem] items-center gap-3 px-4 py-2 text-sm">
              <div className="min-w-0">
                <p className="truncate font-mono text-[12px]" title={f.path}>{f.path}</p>
                <p className="text-muted-foreground text-[11px]">
                  {f.covered} / {f.statements} lines{f.bucket && f.bucket !== "Application source" ? ` · ${f.bucket.toLowerCase()}` : ""}
                </p>
              </div>
              <PctBar pct={f.pct} threshold={threshold} tone={tone} thin />
              <span className={cn("text-right font-semibold tabular-nums",
                tone === "success" ? "text-success" : tone === "warning" ? "text-amber-800 dark:text-amber-300" : "text-red-700 dark:text-red-400")}>
                {f.pct.toFixed(0)}%
              </span>
            </li>
          );
        })}
      </ul>
      {files.length > LIMIT && (
        <button type="button" onClick={() => setShowAll((s) => !s)}
          className="text-primary inline-flex items-center gap-1 text-xs font-medium hover:underline">
          <ChevronDown className={cn("size-3.5 transition-transform", showAll && "rotate-180")} aria-hidden />
          {showAll ? "Show fewer" : `Show all ${files.length} files`}
        </button>
      )}
    </div>
  );
}

/* ── designed cases, grouped by scenario ────────────────────────────────── */

const SCENARIO_ORDER = ["happy path", "positive", "error", "negative", "edge", "boundary"];

function scenarioRank(s: string) {
  const k = s.toLowerCase();
  const i = SCENARIO_ORDER.findIndex((o) => k.includes(o));
  return i === -1 ? SCENARIO_ORDER.length : i;
}

function scenarioTone(s: string): PillTone {
  const k = s.toLowerCase();
  if (k.includes("happy") || k.includes("positive")) return "success";
  if (k.includes("error") || k.includes("negative")) return "danger";
  if (k.includes("edge") || k.includes("boundary")) return "warning";
  return "accent";
}

function stepsList(steps: string): string[] {
  return steps
    .split(/\r?\n|(?=\b\d+[.)]\s)/)
    .map((s) => s.replace(/^\s*\d+[.)]\s*/, "").trim())
    .filter(Boolean);
}

function TestCases({ cases }: { cases: RunReport["testCases"] }) {
  const groups = React.useMemo(() => {
    const m = new Map<string, RunReport["testCases"]>();
    for (const c of cases) {
      const k = c.scenarioType || "Other";
      m.set(k, [...(m.get(k) ?? []), c]);
    }
    return [...m.entries()].sort((a, b) => scenarioRank(a[0]) - scenarioRank(b[0]));
  }, [cases]);
  return (
    <div className="space-y-5">
      {groups.map(([scenario, items]) => (
        <div key={scenario} className="space-y-2">
          <p className="flex items-center gap-2 text-[11px] font-semibold tracking-wide uppercase">
            <Pill tone={scenarioTone(scenario)}>{scenario}</Pill>
            <span className="text-muted-foreground">{items.length}</span>
          </p>
          <ReportTable head={<><th className={cn(th, "w-20")}>ID</th><th className={th}>Case</th><th className={th}>Steps</th><th className={th}>Expected</th></>} dense>
            {items.map((c, i) => {
              const steps = stepsList(c.steps);
              return (
                <tr key={`${c.id}-${i}`}>
                  <td className={cn(td, "font-mono text-[11px] font-semibold whitespace-nowrap")}>{c.id || `TC-${i + 1}`}</td>
                  <td className={td}>
                    <p className="font-medium">{c.summary || c.feature}</p>
                    {c.summary && c.feature && <p className="text-muted-foreground text-[11px]">{c.feature}</p>}
                    {c.data && <p className="text-muted-foreground mt-1 font-mono text-[11px]">{c.data}</p>}
                  </td>
                  <td className={td}>
                    {steps.length > 1 ? (
                      <ol className="list-decimal space-y-0.5 pl-4 text-[13px]">{steps.map((s, j) => <li key={j}>{s}</li>)}</ol>
                    ) : (steps[0] ?? "—")}
                  </td>
                  <td className={cn(td, "text-[13px]")}>{c.expected || "—"}</td>
                </tr>
              );
            })}
          </ReportTable>
        </div>
      ))}
    </div>
  );
}
