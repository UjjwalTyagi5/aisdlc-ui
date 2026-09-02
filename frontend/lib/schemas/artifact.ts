import { z } from "zod";

import { ArtifactId, ProjectId, RunId, UserId } from "./ids";
import { ArtifactType, Phase, Status } from "./enums";
import { Timestamp } from "./primitives";

/**
 * Typed artifact payloads — only the ones the UI renders natively.
 * Anything else rides in `Artifact.body` as opaque text/markdown.
 */
export const StoryBody = z.object({
  kind: z.literal("story"),
  title: z.string(),
  description: z.string(),
  /**
   * What the item actually is ON THE BOARD — "User Story", "Epic", "Task", "Bug".
   *
   * `kind` above is the ARTIFACT shape this renders as; this is the work item type.
   * Board ingestion pulls every item on the board, so this list routinely mixes Epics
   * and Tasks in with real stories — and without this field they all arrived at the
   * Design agent labelled "user stories", which is how three board-configuration
   * chores became requirements to design a system for.
   *
   * Optional: artifacts synthesised before this existed have no type, and a project
   * on a board whose items are genuinely all stories will not miss it.
   */
  workItemType: z.string().optional(),
  acceptanceCriteria: z.array(
    z.object({
      given: z.string(),
      when: z.string(),
      then: z.string(),
    }),
  ),
  traceability: z
    .object({
      jiraIssueKey: z.string().optional(),
      /** Browsable link to the item on its board, resolved at ingest. Absent on
       *  items ingested before it existed, and on providers with no URL template. */
      boardUrl: z.string().url().optional(),
      designArtifactId: ArtifactId.optional(),
      prUrl: z.string().url().optional(),
    })
    .optional(),
});

export const MermaidBody = z.object({
  kind: z.literal("c4_diagram"),
  source: z.string(),
});

export const OpenApiBody = z.object({
  kind: z.literal("openapi_spec"),
  yaml: z.string(),
});

export const DdlBody = z.object({
  kind: z.literal("db_schema"),
  dialect: z.enum(["postgres", "mysql", "mssql"]),
  sql: z.string(),
});

export const AdrBody = z.object({
  kind: z.literal("adr"),
  markdown: z.string(),
});

export const PrFileStatus = z.enum(["added", "modified", "removed", "renamed"]);
export type PrFileStatus = z.infer<typeof PrFileStatus>;

export const PrFile = z.object({
  path: z.string(),
  status: PrFileStatus,
  additions: z.number().int().nonnegative(),
  deletions: z.number().int().nonnegative(),
  original: z.string(),
  modified: z.string(),
  language: z.string().optional(),
});
export type PrFile = z.infer<typeof PrFile>;

export const PrReviewSeverity = z.enum(["blocking", "suggestion", "nit"]);
export type PrReviewSeverity = z.infer<typeof PrReviewSeverity>;

export const PrReviewComment = z.object({
  id: z.string(),
  path: z.string(),
  line: z.number().int().positive(),
  side: z.enum(["LEFT", "RIGHT"]).default("RIGHT"),
  body: z.string(),
  severity: PrReviewSeverity,
  author: z.object({
    kind: z.enum(["user", "agent"]),
    name: z.string(),
    initials: z.string().optional(),
  }),
  createdAt: z.string().datetime({ offset: true }),
  resolved: z.boolean().optional(),
});
export type PrReviewComment = z.infer<typeof PrReviewComment>;

export const PrCheckStatus = z.enum(["pending", "running", "passing", "failing", "neutral"]);
export type PrCheckStatus = z.infer<typeof PrCheckStatus>;

export const PrCheck = z.object({
  name: z.string(),
  status: PrCheckStatus,
  summary: z.string().optional(),
  url: z.string().url().optional(),
  durationMs: z.number().int().nonnegative().optional(),
});
export type PrCheck = z.infer<typeof PrCheck>;

export const PrBody = z.object({
  kind: z.literal("pr"),
  url: z.string().url(),
  branch: z.string(),
  title: z.string(),
  additions: z.number().int().nonnegative(),
  deletions: z.number().int().nonnegative(),
  filesChanged: z.number().int().nonnegative(),
  files: z.array(PrFile).default([]),
  reviewComments: z.array(PrReviewComment).default([]),
  checks: z.array(PrCheck).default([]),
  linkedTicket: z
    .object({ key: z.string(), url: z.string().url() })
    .optional(),
  linkedDesignArtifactId: ArtifactId.optional(),
});

export const TestSuite = z.enum(["unit", "integration", "e2e"]);
export type TestSuite = z.infer<typeof TestSuite>;

export const TestCaseStatus = z.enum(["passing", "failing", "flaky", "skipped"]);
export type TestCaseStatus = z.infer<typeof TestCaseStatus>;

export const TestCase = z.object({
  id: z.string(),
  name: z.string(),
  path: z.string(),
  suite: TestSuite,
  status: TestCaseStatus,
  durationMs: z.number().int().nonnegative().optional(),
  lastRunAt: Timestamp.optional(),
  /** History of pass/fail booleans — newest last. Powers the sparkline. */
  history: z.array(z.boolean()).default([]),
  failure: z
    .object({
      message: z.string(),
      stack: z.string(),
      lastPassingVersion: z.string().optional(),
      lastPassingAt: Timestamp.optional(),
    })
    .optional(),
  /** Source snippet rendered in the test detail pane. */
  source: z.string().optional(),
  language: z.string().optional(),
});
export type TestCase = z.infer<typeof TestCase>;

export const CoverageFile = z.object({
  path: z.string(),
  lines: z.number().int().nonnegative(),
  covered: z.number().int().nonnegative(),
  percent: z.number().min(0).max(100),
  /** "delta" relative to the previous test run — positive = coverage up. */
  delta: z.number().optional(),
});
export type CoverageFile = z.infer<typeof CoverageFile>;

export const TestSetBody = z.object({
  kind: z.literal("test_set"),
  framework: z.enum(["pytest", "jest", "vitest", "playwright"]),
  files: z.array(z.string()).default([]),
  coverageDelta: z.number(),
  tests: z.array(TestCase).default([]),
  coverage: z.array(CoverageFile).default([]),
});

export const RawBody = z.object({
  kind: z.literal("raw"),
  markdown: z.string(),
});

/** A stored file (PDF, DOCX, XLSX, PPTX, PNG…) rather than renderable content.
 *
 *  These used to arrive as a RawBody whose markdown was `[Download artifact](url)` —
 *  the same link the list row already shows as an icon, rendered through AdrViewer so
 *  every PDF was captioned "Architecture Decision Record". Describing the file lets the
 *  client show what it IS and offer exactly one way to fetch it.
 */
export const DocumentBody = z.object({
  kind: z.literal("document"),
  filename: z.string(),
  contentType: z.string().nullish(),
  sizeBytes: z.number().nullish(),
  /** False when the row exists but the upload failed — listed, not downloadable. */
  stored: z.boolean().default(true),
});

// ─── Deployment artifact bodies (Chunk 13) ────────────────────

export const PipelineProvider = z.enum(["github_actions", "azure_pipelines"]);
export type PipelineProvider = z.infer<typeof PipelineProvider>;

export const PipelineBody = z.object({
  kind: z.literal("pipeline"),
  provider: PipelineProvider,
  filename: z.string(),
  yaml: z.string(),
});
export type PipelineBody = z.infer<typeof PipelineBody>;

export const IacDialect = z.enum(["terraform", "pulumi", "bicep"]);
export type IacDialect = z.infer<typeof IacDialect>;

export const IacDiffBody = z.object({
  kind: z.literal("iac_diff"),
  dialect: IacDialect,
  filename: z.string(),
  before: z.string(),
  after: z.string(),
});
export type IacDiffBody = z.infer<typeof IacDiffBody>;

export const DeployEnvName = z.enum(["dev", "staging", "prod"]);
export type DeployEnvName = z.infer<typeof DeployEnvName>;

export const DeployEnv = z.object({
  name: DeployEnvName,
  status: Status,
  /** Prod is always true. Dev may be auto-deploy (false). */
  requiresApproval: z.boolean(),
  approvedBy: z
    .object({
      id: UserId,
      name: z.string(),
      initials: z.string(),
    })
    .optional(),
  approvedAt: Timestamp.optional(),
  commitSha: z.string().optional(),
  runId: RunId.optional(),
  deployedAt: Timestamp.optional(),
});
export type DeployEnv = z.infer<typeof DeployEnv>;

export const DeployPlanBody = z.object({
  kind: z.literal("deploy_plan"),
  /** PR the agent opened with the pipeline + IaC changes. Dry-run only in MVP-0. */
  prUrl: z.string().url().optional(),
  envs: z.array(DeployEnv).min(1),
  rollback: z
    .object({
      markdown: z.string(),
    })
    .optional(),
});
export type DeployPlanBody = z.infer<typeof DeployPlanBody>;

export const ArtifactBody = z.discriminatedUnion("kind", [
  StoryBody,
  MermaidBody,
  OpenApiBody,
  DdlBody,
  AdrBody,
  PrBody,
  TestSetBody,
  PipelineBody,
  IacDiffBody,
  DeployPlanBody,
  DocumentBody,
  RawBody,
]);
export type ArtifactBody = z.infer<typeof ArtifactBody>;

export const Artifact = z.object({
  id: ArtifactId,
  projectId: ProjectId,
  runId: RunId.nullable(),
  type: ArtifactType,
  phase: Phase,
  title: z.string(),
  version: z.number().int().positive(),
  /** sha-256 hex of the rendered content — used for idempotency + change detection. */
  contentHash: z.string().length(64),
  status: Status,
  body: ArtifactBody,
  /** Absolute URL of the generated blob (docx/pptx/png), when the artifact has one. */
  downloadUrl: z.string().nullish(),
  createdBy: UserId.or(z.literal("agent")),
  createdAt: Timestamp,
  updatedAt: Timestamp,
});
export type Artifact = z.infer<typeof Artifact>;
