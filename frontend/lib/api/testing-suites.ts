import { z } from "zod";

import type { Artifact, ProjectId } from "@/lib/schemas";

import { api } from "./client";

/**
 * Test case suites — generate unit / functional / API cases as Excel, follow the job, read a
 * suite back. Backend: shared/routers/testing_suites.py.
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
};

const SUITE_NAME: Record<SuiteKind, RegExp> = {
  unit: /_Unit_Test_Cases(_v\d+)?\.xlsx$/i,
  functional: /_Functional_Test_Cases(_v\d+)?\.xlsx$/i,
  api: /_API_Test_Cases(_v\d+)?\.xlsx$/i,
};

/** The newest test case workbook of each kind in this project's Testing documents. */
export function latestSuites(documents: readonly Artifact[] | null | undefined): Record<SuiteKind, Artifact | null> {
  const rows = (documents ?? [])
    .filter((d) => d.stage === "testing" && d.status !== "rejected")
    .sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
  return {
    unit: rows.find((d) => SUITE_NAME.unit.test(d.title)) ?? null,
    functional: rows.find((d) => SUITE_NAME.functional.test(d.title)) ?? null,
    api: rows.find((d) => SUITE_NAME.api.test(d.title)) ?? null,
  };
}
