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
