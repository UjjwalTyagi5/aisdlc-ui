import { z } from "zod";

import type { ProjectId } from "@/lib/schemas";

import { api } from "./client";

/**
 * Test case suites — generate unit / functional / API cases as Excel, run them, follow the job,
 * list the history, read a suite back. Backend: shared/routers/testing_suites.py.
 */

const enc = encodeURIComponent;

export const SuiteKind = z.enum(["unit", "functional", "api"]);
export type SuiteKind = z.infer<typeof SuiteKind>;
export const SUITE_KINDS: SuiteKind[] = ["unit", "functional", "api"];
export const SUITE_LABEL: Record<SuiteKind, string> = { unit: "Unit", functional: "Functional", api: "API" };

export const SuiteTarget = z.object({
  ado_project: z.string(),
  repo: z.string(),
  branch: z.string(),
  provider: z.string().nullable().optional(),
});
export type SuiteTarget = z.infer<typeof SuiteTarget>;

export const FiledDocument = z.object({
  kind: SuiteKind.optional(),
  name: z.string(),
  url: z.string().default(""),
  artifact_id: z.string(),
  cases: z.number().optional(),
  skipped: z.array(z.string()).default([]),
});

export const SuiteJob = z.object({
  id: z.string(),
  kind: z.string(),
  project_id: z.string(),
  user_id: z.string().default(""),
  user_name: z.string().default(""),
  status: z.enum(["queued", "running", "succeeded", "failed", "interrupted"]),
  created_at: z.string(),
  started_at: z.string().nullable().optional(),
  finished_at: z.string().nullable().optional(),
  progress: z.array(z.object({ at: z.string(), level: z.string().default("info"), message: z.string() })).default([]),
  result: z
    .object({
      documents: z.array(FiledDocument).default([]),
      failures: z.array(z.object({ kind: z.string(), error: z.string() })).default([]),
      commit: z.string().optional(),
    })
    .passthrough()
    .default({ documents: [], failures: [] }),
  error: z.string().default(""),
  params: z.record(z.string(), z.unknown()).default({}),
});
export type SuiteJob = z.infer<typeof SuiteJob>;

export const isActive = (job: SuiteJob | null | undefined) => job?.status === "queued" || job?.status === "running";

export const generateSuites = (
  projectId: ProjectId,
  body: { target: SuiteTarget; kinds: SuiteKind[]; offering_id?: string },
) => api(`/testing/${enc(projectId)}/suites/generate`, { method: "POST", body, schema: SuiteJob });

export const listSuiteJobs = (projectId: ProjectId, kind?: string) =>
  api(`/testing/${enc(projectId)}/suites/jobs`, {
    query: { kind, limit: 10 },
    schema: z.object({ jobs: z.array(SuiteJob) }),
  });

export const getSuiteJob = (projectId: ProjectId, jobId: string) =>
  api(`/testing/${enc(projectId)}/suites/jobs/${enc(jobId)}`, { schema: SuiteJob });

const Step = z.object({ action: z.string(), target: z.string().default(""), value: z.string().default("") });
export const SuiteCase = z
  .object({
    id: z.string(),
    title: z.string(),
    requirement: z.string().default(""),
    priority: z.string().default("Medium"),
    // unit
    module: z.string().optional(),
    function: z.string().optional(),
    scenario: z.string().optional(),
    input: z.string().optional(),
    expected: z.string().optional(),
    // functional
    preconditions: z.string().optional(),
    steps: z.array(Step).optional(),
    // api
    method: z.string().optional(),
    path: z.string().optional(),
    body: z.string().optional(),
    expected_status: z.number().optional(),
    expected_body_contains: z.array(z.string()).optional(),
    capture: z.record(z.string(), z.string()).optional(),
  })
  .passthrough();
export type SuiteCase = z.infer<typeof SuiteCase>;

export const SuiteDocument = z.object({
  documentId: z.string(),
  status: z.string(),
  meta: z.object({
    kind: SuiteKind,
    project: z.string().default(""),
    source_project: z.string().default(""),
    repository: z.string().default(""),
    branch: z.string().default(""),
    commit: z.string().default(""),
    generated_at: z.string().default(""),
    sources: z.string().default(""),
    app_notes: z.string().default(""),
  }),
  cases: z.array(SuiteCase),
  problems: z.array(z.string()).default([]),
});
export type SuiteDocument = z.infer<typeof SuiteDocument>;

export const getSuite = (projectId: ProjectId, documentId: string) =>
  api(`/testing/${enc(projectId)}/suites/${enc(documentId)}`, { schema: SuiteDocument });

export const suitesKeys = {
  jobs: (id: ProjectId) => ["testing", "suite-jobs", id] as const,
  suite: (id: ProjectId, doc: string) => ["testing", "suite", id, doc] as const,
  history: (id: ProjectId) => ["testing", "suite-history", id] as const,
  entry: (id: ProjectId, entry: string) => ["testing", "suite-history", id, "entry", entry] as const,
};

// ── runs ──────────────────────────────────────────────────────────────────────

export const RUN_JOB_KIND: Record<SuiteKind, string> = { unit: "run_unit", functional: "run_functional", api: "run_api" };

export const ResultRowSchema = z.object({
  id: z.string(),
  title: z.string(),
  subject: z.string().default(""),
  status: z.enum(["Passed", "Failed", "Error", "Not run"]),
  duration_ms: z.number().nullable().optional(),
  message: z.string().default(""),
  evidence: z.string().default(""),
});
export type ResultRow = z.infer<typeof ResultRowSchema>;

export const RunResult = z.object({
  verdict: z.string().default(""),
  totals: z.record(z.string(), z.number()).default({}),
  rows: z.array(ResultRowSchema).default([]),
  suite_document: z.string().default(""),
  commit: z.string().default(""),
  target_url: z.string().default(""),
});
export type RunResult = z.infer<typeof RunResult>;

/** A run job's result, when it has one. */
export function runResult(job: SuiteJob | null | undefined): RunResult | null {
  if (!job || job.status !== "succeeded") return null;
  const parsed = RunResult.safeParse(job.result);
  return parsed.success && parsed.data.rows.length ? parsed.data : null;
}

export const runSuite = (
  projectId: ProjectId,
  documentId: string,
  body: { base_url?: string; headless?: boolean; offering_id?: string },
) => api(`/testing/${enc(projectId)}/suites/${enc(documentId)}/run`, { method: "POST", body, schema: SuiteJob });

// ── history ───────────────────────────────────────────────────────────────────

/**
 * One generation and every run of the test cases it filed, newest run first. A run of a
 * workbook no generation filed is an entry of its own, with no generation.
 */
export const HistoryEntry = z.object({
  id: z.string(),
  updated_at: z.string(),
  active: z.boolean().default(false),
  generation: SuiteJob.nullable(),
  runs: z.array(SuiteJob).default([]),
});
export type HistoryEntry = z.infer<typeof HistoryEntry>;

export const HistoryPage = z.object({ entries: z.array(HistoryEntry), total: z.number() });
export type HistoryPage = z.infer<typeof HistoryPage>;

export const listHistory = (projectId: ProjectId, page: { limit?: number; offset?: number } = {}) =>
  api(`/testing/${enc(projectId)}/suites/history`, {
    query: { limit: page.limit ?? 20, offset: page.offset ?? 0 },
    schema: HistoryPage,
  });

export const getHistoryEntry = (projectId: ProjectId, entryId: string) =>
  api(`/testing/${enc(projectId)}/suites/history/${enc(entryId)}`, { schema: HistoryEntry });

const RUN_KIND = Object.fromEntries(Object.entries(RUN_JOB_KIND).map(([k, v]) => [v, k])) as Record<string, SuiteKind>;

/** The suite kind a run job ran. */
export const runKind = (job: SuiteJob): SuiteKind | null => RUN_KIND[job.kind] ?? null;

/** What an entry holds for one kind of test. */
export interface EntryKind {
  /** The workbook the generation filed — or, for an entry without one, the workbook its run used. */
  documentId: string | null;
  documentName: string;
  cases: number | null;
  /** Why the generation did not write this kind, when it tried. */
  failure: string;
  /** Asked for by the generation (so, while it runs, still to come). */
  requested: boolean;
  /** The latest run, then every run, newest first. */
  run: SuiteJob | null;
  runs: SuiteJob[];
  /** The report the latest run filed. */
  reportId: string | null;
  reportName: string;
}

export function entryKinds(entry: HistoryEntry | null | undefined): Record<SuiteKind, EntryKind> {
  const gen = entry?.generation ?? null;
  const kinds = Array.isArray(gen?.params.kinds) ? (gen.params.kinds as string[]) : [];
  const out = {} as Record<SuiteKind, EntryKind>;
  for (const k of SUITE_KINDS) {
    const filed = gen?.result.documents.find((d) => d.kind === k) ?? null;
    const runs = (entry?.runs ?? []).filter((r) => runKind(r) === k);
    const run = runs[0] ?? null;
    const report = run?.result.documents[0] ?? null;
    out[k] = {
      documentId: filed?.artifact_id ?? (gen ? null : (run?.params.document_id as string | undefined) ?? null),
      documentName: filed?.name ?? "",
      cases: filed?.cases ?? null,
      failure: gen?.result.failures.find((f) => f.kind === k)?.error ?? "",
      requested: kinds.includes(k),
      run,
      runs,
      reportId: report?.artifact_id ?? null,
      reportName: report?.name ?? "",
    };
  }
  return out;
}

/** The branch an entry's generation was written for. */
export function entryTarget(entry: HistoryEntry | null | undefined): SuiteTarget | null {
  const parsed = SuiteTarget.safeParse(entry?.generation?.params.target);
  return parsed.success ? parsed.data : null;
}
