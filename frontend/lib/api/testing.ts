import { z } from "zod";

import type { ProjectId } from "@/lib/schemas";
import { api } from "./client";

const enc = encodeURIComponent;

export const UnitResult = z.object({
  available: z.boolean(),
  coverage: z
    .object({
      coverage_pct: z.number().default(0),
      statements: z.number().default(0),
      missed: z.number().default(0),
      branch_coverage_pct: z.number().nullable().optional(),
    })
    .nullable()
    .optional(),
  results: z
    .object({
      total: z.number().default(0),
      passed: z.number().default(0),
      failed: z.number().default(0),
      skipped: z.number().default(0),
    })
    .nullable()
    .optional(),
  generated_files: z.array(z.object({ path: z.string(), bytes: z.number().default(0) })).default([]),
  clone_target: z
    .object({
      project: z.string().nullable().optional(),
      repo: z.string().nullable().optional(),
      branch: z.string().nullable().optional(),
    })
    .nullable()
    .optional(),
  pr_url: z.string().nullable().optional(),
});
export type UnitResult = z.infer<typeof UnitResult>;

export const getUnitResult = (projectId: ProjectId, sessionId: string) =>
  api(`/testing/${enc(projectId)}/unit-result/${enc(sessionId)}`, { schema: UnitResult });

export const TestsPrResult = z.object({
  pr_url: z.string().nullable().optional(),
  already: z.boolean().optional(),
  files: z.number().optional(),
  branch: z.string().optional(),
});
export type TestsPrResult = z.infer<typeof TestsPrResult>;

export const openTestsPr = (projectId: ProjectId, sessionId: string) =>
  api(`/testing/${enc(projectId)}/tests-pr/${enc(sessionId)}`, { method: "POST", schema: TestsPrResult });

/* ── the run report ────────────────────────────────────────────────────────
 * The numbers a run produced, shaped for the page: verdict, execution totals, each
 * test, each file's coverage, the generated cases, the defects. Read from the run's
 * `run_report.json` (see backend testing_agent/run_report.py); never from the agent's
 * prose, which is what the Output pane used to render as raw text.
 */

const TestStatus = z.enum(["passed", "failed", "error", "skipped"]);
export type TestStatus = z.infer<typeof TestStatus>;

export const RunVerdict = z.enum(["passed", "failed", "partial", "no_tests", "error"]);
export type RunVerdict = z.infer<typeof RunVerdict>;

export const RunReport = z.object({
  sessionId: z.string(),
  verdict: RunVerdict,
  testTypes: z.array(z.string()).default([]),
  language: z.string().default(""),
  framework: z.string().default(""),
  runnerCommand: z.string().default(""),
  target: z.object({
    project: z.string().default(""),
    repo: z.string().default(""),
    branch: z.string().default(""),
  }),
  execution: z.object({
    total: z.number().default(0),
    passed: z.number().default(0),
    failed: z.number().default(0),
    skipped: z.number().default(0),
    errors: z.number().default(0),
    durationMs: z.number().default(0),
  }),
  coverage: z.object({
    linePct: z.number().default(0),
    branchPct: z.number().nullable().optional(),
    statements: z.number().default(0),
    missed: z.number().default(0),
    applicationPct: z.number().nullable().optional(),
    thresholdPct: z.number().nullable().optional(),
    files: z
      .array(
        z.object({
          path: z.string(),
          pct: z.number().default(0),
          statements: z.number().default(0),
          covered: z.number().default(0),
          missed: z.number().default(0),
          bucket: z.string().default(""),
        }),
      )
      .default([]),
  }),
  tests: z
    .array(
      z.object({
        name: z.string(),
        suite: z.string().default(""),
        status: TestStatus.catch("passed"),
        durationMs: z.number().default(0),
        message: z.string().default(""),
      }),
    )
    .default([]),
  testCases: z
    .array(
      z.object({
        id: z.string().default(""),
        feature: z.string().default(""),
        summary: z.string().default(""),
        scenarioType: z.string().default(""),
        steps: z.string().default(""),
        data: z.string().default(""),
        expected: z.string().default(""),
      }),
    )
    .default([]),
  defects: z
    .array(
      z.object({
        id: z.string().default(""),
        severity: z.string().default("medium"),
        summary: z.string().default(""),
        detail: z.string().default(""),
      }),
    )
    .default([]),
  unrunnableSuites: z.array(z.object({ file: z.string(), reason: z.string().default("") })).default([]),
  generatedFiles: z.array(z.string()).default([]),
  artifactFiles: z.array(z.string()).default([]),
  qaReportAvailable: z.boolean().default(false),
  lintExit: z.number().nullable().optional(),
  summaryMd: z.string().default(""),
});
export type RunReport = z.infer<typeof RunReport>;

/** `available: false` while the run has produced nothing yet — the page keeps
 *  showing progress rather than an empty report. */
export const RunReportResponse = z.object({
  available: z.boolean(),
  report: RunReport.nullable().optional(),
});
export type RunReportResponse = z.infer<typeof RunReportResponse>;

export const getRunReport = (projectId: ProjectId, sessionId: string) =>
  api(`/testing/${enc(projectId)}/report/${enc(sessionId)}`, { schema: RunReportResponse });
