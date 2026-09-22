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
  /** A/M/D/R for a diff; "T" (tracked) for a whole-branch file, whose `added` is its lines. */
  status: z.string(),
  added: z.number(),
  removed: z.number(),
  language: z.string().optional(),
  reviewable: z.boolean().optional(),
});
export type ChangedFile = z.infer<typeof ChangedFile>;

/** "branch" and "pr" review a diff; "repo" reviews a WHOLE BRANCH (no diff). */
export const ReviewMode = z.enum(["branch", "pr", "repo"]);
export type ReviewMode = z.infer<typeof ReviewMode>;

export const PrepareResult = z.object({
  /** "ready", or "no_changes" — a diff target with nothing in it, nothing bound. */
  status: z.string(),
  no_changes_reason: z.string().nullable().optional(),
  mode: ReviewMode,
  repo_name: z.string(),
  ado_project: z.string(),
  source_branch: z.string(),
  base_branch: z.string(),
  pr_id: z.string().nullable().optional(),
  pr_title: z.string().nullable().optional(),
  head_sha: z.string(),
  base_sha: z.string(),
  commits_ahead: z.number().optional(),
  files: z.array(ChangedFile),
  diff: z.string(),
  truncated: z.boolean(),
  inventory_totals: z
    .object({ files: z.number(), reviewable_files: z.number(), lines: z.number() })
    .partial()
    .optional(),
  languages: z.record(z.string(), z.number()).optional(),
  // True when this exact diff (same repo + head + base sha) was already reviewed —
  // PRD §21.4: "Skips redundant re-review when nothing changed since the last pass."
  unchanged_since_last_review: z.boolean().default(false),
  existing_review_id: z.string().nullable().optional(),
  // The branch that review ran against — often a DIFFERENT name pointing at the same
  // commit, which is the case that makes an unexplained "nothing changed" confusing.
  existing_review_branch: z.string().nullable().optional(),
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

export const ReviewScope = z
  .object({
    mode: z.string(),
    files_read: z.array(z.string()),
    languages: z.record(z.string(), z.number()),
    files_total: z.number(),
    reviewable_files: z.number(),
    lines_total: z.number(),
    reviewable_files_read: z.number(),
    not_read: z.array(z.string()),
    files_changed: z.number(),
    changed_files: z.array(z.string()),
    /** The approved requirements/design documents the code was checked against; outcome
     *  "ok" or why the document could not be read. Absent on reviews saved before it. */
    documents: z.array(z.object({ title: z.string(), stage: z.string(), outcome: z.string() })),
  })
  .partial();
export type ReviewScope = z.infer<typeof ReviewScope>;

export const CodeReviewArtifact = z.object({
  id: z.string().optional(),
  created_at: z.string().optional(),
  context: z.object({
    repo_name: z.string().default(""),
    ado_project: z.string().default(""),
    mode: ReviewMode.default("branch"),
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
  scope: ReviewScope.default({}),
  /** The Code Review Report: its link, or why there is none. */
  document: z
    /** `artifact_id` is the report's own document row — its approval status is read from
     *  it. Absent on reviews saved before 16 Sep 2026 (see reportDocumentFor). */
    .object({ filename: z.string(), url: z.string(), error: z.string(), artifact_id: z.string().nullable() })
    .partial()
    .default({}),
});
export type CodeReviewArtifact = z.infer<typeof CodeReviewArtifact>;
