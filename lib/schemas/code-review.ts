import { z } from "zod";

export const AdoPr = z.object({
  id: z.string(),
  title: z.string(),
  source_branch: z.string(),
  target_branch: z.string(),
  created_by: z.string().optional().default(""),
});
export type AdoPr = z.infer<typeof AdoPr>;

export const ChangedFile = z.object({
  path: z.string(),
  status: z.string(),
  added: z.number(),
  removed: z.number(),
});
export type ChangedFile = z.infer<typeof ChangedFile>;

export const PrepareResult = z.object({
  status: z.string(),
  mode: z.enum(["branch", "pr"]),
  repo_name: z.string(),
  ado_project: z.string(),
  source_branch: z.string(),
  base_branch: z.string(),
  pr_id: z.string().nullable().optional(),
  pr_title: z.string().nullable().optional(),
  head_sha: z.string(),
  base_sha: z.string(),
  files: z.array(ChangedFile),
  diff: z.string(),
  truncated: z.boolean(),
});
export type PrepareResult = z.infer<typeof PrepareResult>;

export const MergeRecommendation = z.enum([
  "approve",
  "request_changes",
  "needs_discussion",
]);
export type MergeRecommendation = z.infer<typeof MergeRecommendation>;

export const ReviewSummaryRow = z.object({
  id: z.string(),
  label: z.string(),
  repo_name: z.string(),
  merge_recommendation: MergeRecommendation,
  findings_count: z.number(),
  critical_high: z.number(),
  created_at: z.string(),
});
export type ReviewSummaryRow = z.infer<typeof ReviewSummaryRow>;

export const Severity = z.enum(["critical", "high", "medium", "low", "info"]);
export type Severity = z.infer<typeof Severity>;

export const ReviewFinding = z.object({
  id: z.string(),
  severity: Severity,
  category: z.string(),
  file: z.string().default(""),
  line: z.number().default(0),
  description: z.string(),
  recommendation: z.string().default(""),
  autofix_patch: z.string().nullable().optional(),
});
export type ReviewFinding = z.infer<typeof ReviewFinding>;

export const CodeReviewArtifact = z.object({
  id: z.string().optional(),
  created_at: z.string().optional(),
  context: z.object({
    repo_name: z.string().default(""),
    ado_project: z.string().default(""),
    mode: z.enum(["branch", "pr"]).default("branch"),
    source_branch: z.string().default(""),
    base_branch: z.string().default(""),
    pr_id: z.string().nullable().optional(),
    pr_title: z.string().nullable().optional(),
    head_sha: z.string().default(""),
    base_sha: z.string().default(""),
  }),
  summary: z.string().default(""),
  merge_recommendation: MergeRecommendation,
  findings: z.array(ReviewFinding).default([]),
  requirements_coverage: z
    .array(z.object({ ac_id: z.string(), status: z.string(), note: z.string().default("") }))
    .default([]),
  design_conformance: z
    .array(z.object({ rule: z.string(), status: z.string(), note: z.string().default("") }))
    .default([]),
  metrics: z.object({
    files_changed: z.number().default(0),
    added: z.number().default(0),
    removed: z.number().default(0),
    complexity_delta: z.number().nullable().optional(),
    dupe_delta: z.number().nullable().optional(),
    debt_delta: z.number().nullable().optional(),
  }),
  diff: z.string().default(""),
  status: z.string().default("reviewed"),
});
export type CodeReviewArtifact = z.infer<typeof CodeReviewArtifact>;
